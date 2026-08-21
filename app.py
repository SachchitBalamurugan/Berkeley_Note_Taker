import io
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import pymupdf
from flask import Flask, abort, g, has_app_context, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | {"pdf"}
RENDER_DPI = 150
MAX_DOCUMENT_PAGES = 300
BLANK_PAGE = (612.0, 792.0)


def hex_to_rgb(value):
    value = (value or "").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (38, 50, 56)


def load_font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap_text(draw, text, font, max_width):
    lines = []
    for paragraph in (text or "").split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=str(BASE_DIR / "whiteboard.db"),
        UPLOAD_FOLDER=str(UPLOAD_DIR),
        MAX_CONTENT_LENGTH=40 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    def uploads_path(name):
        return Path(app.config["UPLOAD_FOLDER"]) / name

    def connect():
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # A request that aborts mid-transaction never reaches its own close(), so
        # hand every connection to the teardown hook as a backstop.
        if has_app_context():
            g.setdefault("open_connections", []).append(conn)
        return conn

    @app.teardown_appcontext
    def close_connections(exception=None):
        for conn in g.pop("open_connections", []):
            conn.close()

    def rows(conn, sql, params=()):
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def init_db():
        conn = connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS notebooks (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY, notebook_id INTEGER NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
                title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, source_name TEXT
            );
            CREATE TABLE IF NOT EXISTS doc_pages (
                id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL, image_name TEXT,
                width REAL NOT NULL DEFAULT 612, height REAL NOT NULL DEFAULT 792
            );
            CREATE TABLE IF NOT EXISTS board_items (
                id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('note','image')), content TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#fff3a8', x REAL NOT NULL DEFAULT 120, y REAL NOT NULL DEFAULT 100,
                width REAL NOT NULL DEFAULT 260, height REAL NOT NULL DEFAULT 180, position INTEGER NOT NULL DEFAULT 0,
                image_name TEXT, doc_page_id INTEGER, font_size REAL NOT NULL DEFAULT 16
            );
            CREATE TABLE IF NOT EXISTS strokes (
                id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                tool TEXT NOT NULL CHECK(tool IN ('pen','highlighter')), color TEXT NOT NULL,
                width REAL NOT NULL, points TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                doc_page_id INTEGER
            );
        """)
        # Indexes come after the migration: on an older database the columns they
        # cover do not exist until migrate() adds them.
        migrate(conn)
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_doc_pages_page ON doc_pages(page_id);
            CREATE INDEX IF NOT EXISTS idx_items_doc_page ON board_items(doc_page_id);
            CREATE INDEX IF NOT EXISTS idx_strokes_doc_page ON strokes(doc_page_id);
        """)
        conn.commit()
        conn.close()

    def migrate(conn):
        """Carry boards made by the infinite-whiteboard version into the document model."""
        for table, column, ddl in (
            ("pages", "source_name", "source_name TEXT"),
            ("board_items", "doc_page_id", "doc_page_id INTEGER"),
            ("board_items", "font_size", "font_size REAL NOT NULL DEFAULT 16"),
            ("strokes", "doc_page_id", "doc_page_id INTEGER"),
        ):
            if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        strays = [row[0] for row in conn.execute("SELECT id FROM pages WHERE id NOT IN (SELECT page_id FROM doc_pages)")]
        for page_id in strays:
            right = bottom = 0.0
            for item in conn.execute("SELECT x, y, width, height FROM board_items WHERE page_id = ?", (page_id,)):
                right, bottom = max(right, item[0] + item[2]), max(bottom, item[1] + item[3])
            for stroke in conn.execute("SELECT points FROM strokes WHERE page_id = ?", (page_id,)):
                for point in json.loads(stroke[0]):
                    right, bottom = max(right, point["x"]), max(bottom, point["y"])
            cursor = conn.execute(
                "INSERT INTO doc_pages(page_id, page_number, image_name, width, height) VALUES (?, 1, NULL, ?, ?)",
                (page_id, max(BLANK_PAGE[0], right + 60), max(BLANK_PAGE[1], bottom + 60)))
            conn.execute("UPDATE board_items SET doc_page_id = ? WHERE page_id = ?", (cursor.lastrowid, page_id))
            conn.execute("UPDATE strokes SET doc_page_id = ? WHERE page_id = ?", (cursor.lastrowid, page_id))

    def payload():
        return request.get_json(silent=True) or {}

    def get_document(conn, page_id):
        document = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not document:
            abort(404, "Document not found")
        return document

    def resolve_doc_page(conn, page_id, requested):
        """Annotations belong to one page of the document; fall back to its first page."""
        if requested:
            row = conn.execute("SELECT id FROM doc_pages WHERE id = ? AND page_id = ?", (requested, page_id)).fetchone()
            if not row:
                abort(400, "That page is not part of this document")
            return row[0]
        row = conn.execute("SELECT id FROM doc_pages WHERE page_id = ? ORDER BY page_number, id LIMIT 1", (page_id,)).fetchone()
        return row[0] if row else None

    def append_blank_page(conn, page_id, width=BLANK_PAGE[0], height=BLANK_PAGE[1]):
        number = conn.execute("SELECT COALESCE(MAX(page_number), 0) + 1 FROM doc_pages WHERE page_id = ?", (page_id,)).fetchone()[0]
        cursor = conn.execute("INSERT INTO doc_pages(page_id, page_number, image_name, width, height) VALUES (?, ?, NULL, ?, ?)",
                              (page_id, number, width, height))
        return dict(conn.execute("SELECT * FROM doc_pages WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def serialise_document(conn, page_id):
        document = dict(get_document(conn, page_id))
        doc_pages = rows(conn, "SELECT * FROM doc_pages WHERE page_id = ? ORDER BY page_number, id", (page_id,))
        items = rows(conn, "SELECT * FROM board_items WHERE page_id = ? ORDER BY position, id", (page_id,))
        strokes = rows(conn, "SELECT * FROM strokes WHERE page_id = ? ORDER BY position, id", (page_id,))
        for stroke in strokes:
            stroke["points"] = json.loads(stroke["points"])
        by_id = {doc_page["id"]: doc_page for doc_page in doc_pages}
        for doc_page in doc_pages:
            doc_page["items"], doc_page["strokes"] = [], []
        fallback = doc_pages[0] if doc_pages else None
        for records, key in ((items, "items"), (strokes, "strokes")):
            for record in records:
                target = by_id.get(record["doc_page_id"]) or fallback
                if target:
                    target[key].append(record)
        document["pages"] = doc_pages
        document["items"], document["strokes"] = items, strokes
        return document

    def store_document_file(conn, page_id, file, extension):
        """Render an upload into one doc_pages row per page of the source file."""
        data = file.read()
        rendered = []
        if extension == "pdf":
            try:
                pdf = pymupdf.open(stream=data, filetype="pdf")
            except Exception:
                abort(400, "That PDF could not be opened")
            if pdf.needs_pass:
                pdf.close()
                abort(400, "Password-protected PDFs are not supported")
            if not pdf.page_count:
                pdf.close()
                abort(400, "That PDF has no pages")
            for number in range(min(pdf.page_count, MAX_DOCUMENT_PAGES)):
                pdf_page = pdf[number]
                image_name = f"{uuid.uuid4().hex}.png"
                pdf_page.get_pixmap(dpi=RENDER_DPI).save(str(uploads_path(image_name)))
                rendered.append((number + 1, image_name, pdf_page.rect.width, pdf_page.rect.height))
            pdf.close()
        else:
            try:
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.size
            except Exception:
                abort(400, "That image could not be opened")
            image_name = f"{uuid.uuid4().hex}.{extension}"
            uploads_path(image_name).write_bytes(data)
            rendered.append((1, image_name, float(width), float(height)))
        for number, image_name, width, height in rendered:
            conn.execute("INSERT INTO doc_pages(page_id, page_number, image_name, width, height) VALUES (?, ?, ?, ?, ?)",
                         (page_id, number, image_name, width, height))
        return rows(conn, "SELECT * FROM doc_pages WHERE page_id = ? ORDER BY page_number, id", (page_id,))

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/notebooks")
    def list_notebooks():
        conn = connect()
        notebooks = rows(conn, "SELECT * FROM notebooks ORDER BY position, id")
        for notebook in notebooks:
            notebook["pages"] = rows(conn, """
                SELECT p.*, (SELECT COUNT(*) FROM doc_pages d WHERE d.page_id = p.id) AS page_count
                FROM pages p WHERE p.notebook_id = ? ORDER BY p.position, p.id""", (notebook["id"],))
        conn.close()
        return jsonify(notebooks)

    @app.post("/api/notebooks")
    def create_notebook():
        title = str(payload().get("title", "Untitled folder")).strip()[:100] or "Untitled folder"
        conn = connect()
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM notebooks").fetchone()[0]
        cursor = conn.execute("INSERT INTO notebooks(title, position) VALUES (?, ?)", (title, pos))
        conn.commit(); notebook = dict(conn.execute("SELECT * FROM notebooks WHERE id = ?", (cursor.lastrowid,)).fetchone()); conn.close()
        return jsonify(notebook), 201

    @app.patch("/api/notebooks/<int:notebook_id>")
    def update_notebook(notebook_id):
        title = str(payload().get("title", "")).strip()[:100]
        if not title: abort(400, "A folder name is required")
        conn = connect(); cursor = conn.execute("UPDATE notebooks SET title = ? WHERE id = ?", (title, notebook_id)); conn.commit()
        if not cursor.rowcount: abort(404, "Folder not found")
        conn.close(); return jsonify({"id": notebook_id, "title": title})

    @app.delete("/api/notebooks/<int:notebook_id>")
    def delete_notebook(notebook_id):
        conn = connect()
        names = [r[0] for r in conn.execute("""
            SELECT image_name FROM board_items
            WHERE image_name IS NOT NULL AND page_id IN (SELECT id FROM pages WHERE notebook_id = ?)
            UNION ALL
            SELECT image_name FROM doc_pages
            WHERE image_name IS NOT NULL AND page_id IN (SELECT id FROM pages WHERE notebook_id = ?)
            """, (notebook_id, notebook_id))]
        cursor = conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,)); conn.commit(); conn.close()
        if not cursor.rowcount: abort(404, "Folder not found")
        for name in names:
            uploads_path(name).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/notebooks/<int:notebook_id>/pages")
    def create_page(notebook_id):
        """Start a blank document: one empty letter-sized page to annotate."""
        title = str(payload().get("title", "Untitled document")).strip()[:100] or "Untitled document"
        conn = connect()
        if not conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone(): abort(404, "Folder not found")
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pages WHERE notebook_id = ?", (notebook_id,)).fetchone()[0]
        cursor = conn.execute("INSERT INTO pages(notebook_id, title, position) VALUES (?, ?, ?)", (notebook_id, title, pos))
        append_blank_page(conn, cursor.lastrowid)
        conn.commit()
        document = dict(conn.execute("SELECT * FROM pages WHERE id = ?", (cursor.lastrowid,)).fetchone()); conn.close()
        return jsonify(document), 201

    @app.post("/api/notebooks/<int:notebook_id>/documents")
    def upload_document(notebook_id):
        file = request.files.get("document")
        if not file or not file.filename: abort(400, "Choose a PDF or image to upload")
        safe_name = secure_filename(file.filename) or "document"
        extension = Path(safe_name).suffix.lower().lstrip(".")
        if extension not in DOCUMENT_EXTENSIONS: abort(400, "Upload a PDF, PNG, JPEG, GIF, or WebP file")
        conn = connect()
        if not conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone(): abort(404, "Folder not found")
        title = str(request.form.get("title", "")).strip()[:100] or Path(safe_name).stem[:100] or "Untitled document"
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pages WHERE notebook_id = ?", (notebook_id,)).fetchone()[0]
        cursor = conn.execute("INSERT INTO pages(notebook_id, title, position, source_name) VALUES (?, ?, ?, ?)",
                              (notebook_id, title, pos, safe_name))
        page_id = cursor.lastrowid
        doc_pages = store_document_file(conn, page_id, file, extension)
        conn.commit()
        document = dict(conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone())
        conn.close()
        document["pages"] = doc_pages
        return jsonify(document), 201

    @app.patch("/api/pages/<int:page_id>")
    def update_page(page_id):
        title = str(payload().get("title", "")).strip()[:100]
        if not title: abort(400, "A document name is required")
        conn = connect(); cursor = conn.execute("UPDATE pages SET title = ? WHERE id = ?", (title, page_id)); conn.commit()
        if not cursor.rowcount: abort(404, "Document not found")
        conn.close(); return jsonify({"id": page_id, "title": title})

    @app.delete("/api/pages/<int:page_id>")
    def delete_page(page_id):
        conn = connect()
        names = [r[0] for r in conn.execute("""
            SELECT image_name FROM board_items WHERE page_id = ? AND image_name IS NOT NULL
            UNION ALL SELECT image_name FROM doc_pages WHERE page_id = ? AND image_name IS NOT NULL
            """, (page_id, page_id))]
        cursor = conn.execute("DELETE FROM pages WHERE id = ?", (page_id,)); conn.commit(); conn.close()
        if not cursor.rowcount: abort(404, "Document not found")
        for name in names: uploads_path(name).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/pages/<int:page_id>/blank-page")
    def add_blank_page(page_id):
        conn = connect(); get_document(conn, page_id)
        last = conn.execute("SELECT width, height FROM doc_pages WHERE page_id = ? ORDER BY page_number DESC, id DESC LIMIT 1", (page_id,)).fetchone()
        size = (last["width"], last["height"]) if last else BLANK_PAGE
        doc_page = append_blank_page(conn, page_id, *size)
        conn.commit(); conn.close()
        doc_page["items"], doc_page["strokes"] = [], []
        return jsonify(doc_page), 201

    @app.delete("/api/doc-pages/<int:doc_page_id>")
    def delete_doc_page(doc_page_id):
        conn = connect()
        doc_page = conn.execute("SELECT * FROM doc_pages WHERE id = ?", (doc_page_id,)).fetchone()
        if not doc_page: abort(404, "Page not found")
        if conn.execute("SELECT COUNT(*) FROM doc_pages WHERE page_id = ?", (doc_page["page_id"],)).fetchone()[0] < 2:
            abort(400, "A document needs at least one page")
        names = [r[0] for r in conn.execute("SELECT image_name FROM board_items WHERE doc_page_id = ? AND image_name IS NOT NULL", (doc_page_id,))]
        if doc_page["image_name"]: names.append(doc_page["image_name"])
        conn.execute("DELETE FROM board_items WHERE doc_page_id = ?", (doc_page_id,))
        conn.execute("DELETE FROM strokes WHERE doc_page_id = ?", (doc_page_id,))
        conn.execute("DELETE FROM doc_pages WHERE id = ?", (doc_page_id,))
        remaining = [r[0] for r in conn.execute("SELECT id FROM doc_pages WHERE page_id = ? ORDER BY page_number, id", (doc_page["page_id"],))]
        for number, row_id in enumerate(remaining, start=1):
            conn.execute("UPDATE doc_pages SET page_number = ? WHERE id = ?", (number, row_id))
        conn.commit(); conn.close()
        for name in names: uploads_path(name).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/notebooks/reorder")
    def reorder_notebooks():
        ids = payload().get("ids", []); conn = connect()
        for pos, item_id in enumerate(ids): conn.execute("UPDATE notebooks SET position = ? WHERE id = ?", (pos, item_id))
        conn.commit(); conn.close(); return "", 204

    @app.post("/api/pages/reorder")
    def reorder_pages():
        ids = payload().get("ids", []); conn = connect()
        for pos, item_id in enumerate(ids): conn.execute("UPDATE pages SET position = ? WHERE id = ?", (pos, item_id))
        conn.commit(); conn.close(); return "", 204

    @app.get("/api/pages/<int:page_id>")
    def read_page(page_id):
        conn = connect(); result = serialise_document(conn, page_id); conn.close(); return jsonify(result)

    @app.post("/api/pages/<int:page_id>/items")
    def create_item(page_id):
        data = payload(); kind = data.get("kind", "note")
        if kind != "note": abort(400, "Use the upload endpoint for images")
        conn = connect(); get_document(conn, page_id)
        doc_page_id = resolve_doc_page(conn, page_id, data.get("doc_page_id"))
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM board_items WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("""INSERT INTO board_items(page_id, doc_page_id, kind, content, color, x, y, width, height, position, font_size)
            VALUES (?, ?, 'note', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (page_id, doc_page_id, str(data.get("content", "")).strip(), data.get("color", "#fff3a8"), data.get("x", 60),
             data.get("y", 60), data.get("width", 220), data.get("height", 140), pos, data.get("font_size", 16)))
        conn.commit(); item = dict(conn.execute("SELECT * FROM board_items WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close()
        return jsonify(item), 201

    @app.patch("/api/items/<int:item_id>")
    def update_item(item_id):
        data = payload(); allowed = {"content", "color", "x", "y", "width", "height", "position", "font_size", "doc_page_id"}
        updates = [(key, data[key]) for key in allowed if key in data]
        if not updates: abort(400, "No item fields supplied")
        sql = ", ".join(f"{key} = ?" for key, _ in updates)
        conn = connect(); cursor = conn.execute(f"UPDATE board_items SET {sql} WHERE id = ?", [value for _, value in updates] + [item_id]); conn.commit()
        if not cursor.rowcount: abort(404, "Item not found")
        item = dict(conn.execute("SELECT * FROM board_items WHERE id = ?", (item_id,)).fetchone()); conn.close(); return jsonify(item)

    @app.delete("/api/items/<int:item_id>")
    def delete_item(item_id):
        conn = connect(); item = conn.execute("SELECT image_name FROM board_items WHERE id = ?", (item_id,)).fetchone()
        if not item: abort(404, "Item not found")
        conn.execute("DELETE FROM board_items WHERE id = ?", (item_id,)); conn.commit(); conn.close()
        if item["image_name"]: uploads_path(item["image_name"]).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/pages/<int:page_id>/upload")
    def upload_image(page_id):
        """Stamp an image — a signature, photo, or seal — onto one page of the document."""
        file = request.files.get("image")
        if not file or not file.filename: abort(400, "Choose an image to upload")
        ext = Path(secure_filename(file.filename)).suffix.lower().lstrip(".")
        if ext not in IMAGE_EXTENSIONS or file.mimetype not in IMAGE_MIMES: abort(400, "Only PNG, JPEG, GIF, and WebP images are allowed")
        conn = connect(); get_document(conn, page_id)
        doc_page_id = resolve_doc_page(conn, page_id, request.form.get("doc_page_id", type=int))
        image_name = f"{uuid.uuid4().hex}.{ext}"; file.save(uploads_path(image_name))
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM board_items WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("""INSERT INTO board_items(page_id, doc_page_id, kind, content, x, y, width, height, position, image_name)
            VALUES (?, ?, 'image', '', ?, ?, ?, ?, ?, ?)""",
            (page_id, doc_page_id, request.form.get("x", 60, type=float), request.form.get("y", 60, type=float),
             request.form.get("width", 240, type=float), request.form.get("height", 160, type=float), pos, image_name))
        conn.commit(); item = dict(conn.execute("SELECT * FROM board_items WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close()
        return jsonify(item), 201

    @app.get("/uploads/<path:filename>")
    def uploads(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.post("/api/pages/<int:page_id>/strokes")
    def create_stroke(page_id):
        data = payload(); points = data.get("points", [])
        if data.get("tool") not in {"pen", "highlighter"} or not isinstance(points, list) or len(points) < 2:
            abort(400, "A valid stroke requires at least two points")
        conn = connect(); get_document(conn, page_id)
        doc_page_id = resolve_doc_page(conn, page_id, data.get("doc_page_id"))
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM strokes WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("INSERT INTO strokes(page_id, doc_page_id, tool, color, width, points, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (page_id, doc_page_id, data["tool"], data.get("color", "#263238"), max(1, min(float(data.get("width", 4)), 40)), json.dumps(points), pos))
        conn.commit(); stroke = dict(conn.execute("SELECT * FROM strokes WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close()
        stroke["points"] = json.loads(stroke["points"]); return jsonify(stroke), 201

    @app.delete("/api/strokes/<int:stroke_id>")
    def delete_stroke(stroke_id):
        conn = connect(); cursor = conn.execute("DELETE FROM strokes WHERE id = ?", (stroke_id,)); conn.commit(); conn.close()
        if not cursor.rowcount: abort(404, "Stroke not found")
        return "", 204

    @app.get("/api/search")
    def search():
        term = request.args.get("q", "").strip()
        if not term: return jsonify([])
        like = f"%{term}%"; conn = connect()
        results = rows(conn, """SELECT DISTINCT p.id AS page_id, p.title AS page_title, n.id AS notebook_id, n.title AS notebook_title
            FROM pages p JOIN notebooks n ON n.id = p.notebook_id LEFT JOIN board_items b ON b.page_id = p.id
            WHERE n.title LIKE ? OR p.title LIKE ? OR (b.kind = 'note' AND b.content LIKE ?) ORDER BY n.position, p.position""", (like, like, like))
        conn.close(); return jsonify(results)

    @app.post("/api/sync")
    def sync_to_github():
        try:
            cwd = str(BASE_DIR)
            subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, check=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result = subprocess.run(["git", "commit", "-m", f"Auto-sync: All changes ({timestamp})"], cwd=cwd, capture_output=True, text=True)
            if result.returncode == 0 or "nothing to commit" in result.stdout.lower() or "nothing to commit" in result.stderr.lower():
                push = subprocess.run(["git", "push", "origin", "main"], cwd=cwd, capture_output=True, text=True)
                if push.returncode == 0: return jsonify({"status": "success", "message": "All changes pushed to GitHub"}), 200
                else: return jsonify({"status": "error", "message": push.stderr or push.stdout}), 400
            else: return jsonify({"status": "error", "message": result.stderr or result.stdout}), 400
        except subprocess.CalledProcessError as e:
            return jsonify({"status": "error", "message": e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def flatten_doc_page(doc_page):
        """Burn strokes, notes, and stamps onto the rendered page image."""
        width, height = doc_page["width"], doc_page["height"]
        if doc_page["image_name"] and uploads_path(doc_page["image_name"]).exists():
            base = Image.open(uploads_path(doc_page["image_name"])).convert("RGBA")
        else:
            base = Image.new("RGBA", (int(width * 2), int(height * 2)), "white")
        scale = base.width / width
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        pen = ImageDraw.Draw(overlay, "RGBA")
        for stroke in doc_page["strokes"]:
            points = [(p["x"] * scale, p["y"] * scale) for p in stroke["points"]]
            if len(points) < 2: continue
            alpha = 82 if stroke["tool"] == "highlighter" else 255
            pen.line(points, fill=hex_to_rgb(stroke["color"]) + (alpha,), width=max(1, int(stroke["width"] * scale)), joint="curve")
        base = Image.alpha_composite(base, overlay)
        pen = ImageDraw.Draw(base, "RGBA")
        for item in doc_page["items"]:
            left, top = item["x"] * scale, item["y"] * scale
            box_width, box_height = max(1, int(item["width"] * scale)), max(1, int(item["height"] * scale))
            if item["kind"] == "image":
                if item["image_name"] and uploads_path(item["image_name"]).exists():
                    try:
                        with Image.open(uploads_path(item["image_name"])) as stamp:
                            base.alpha_composite(stamp.convert("RGBA").resize((box_width, box_height)), (int(left), int(top)))
                    except OSError:
                        pass
                continue
            font = load_font(max(6, int(item["font_size"] * scale)))
            padding = 10 * scale
            if item["color"] != "transparent":
                pen.rectangle([left, top, left + box_width, top + box_height],
                              fill=hex_to_rgb(item["color"]) + (235,), outline=(0, 0, 0, 40))
            text_top = top + padding
            for line in wrap_text(pen, item["content"], font, box_width - padding * 2):
                pen.text((left + padding, text_top), line, font=font, fill=(31, 41, 55, 255))
                text_top += font.size * 1.35
        return base.convert("RGB")

    def documents_to_pdf(documents):
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=BLANK_PAGE)
        for document in documents:
            for doc_page in document["pages"]:
                width, height = doc_page["width"], doc_page["height"]
                pdf.setPageSize((width, height))
                pdf.drawImage(ImageReader(flatten_doc_page(doc_page)), 0, 0, width=width, height=height)
                pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return buffer

    def download_name(title):
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
        return f"{safe or 'document'}.pdf"

    @app.get("/api/pages/<int:page_id>/export")
    def export_document(page_id):
        conn = connect(); document = serialise_document(conn, page_id); conn.close()
        if not document["pages"]: abort(400, "That document has no pages")
        return send_file(documents_to_pdf([document]), mimetype="application/pdf",
                         as_attachment=True, download_name=download_name(document["title"]))

    @app.get("/api/notebooks/<int:notebook_id>/export")
    def export_notebook(notebook_id):
        conn = connect()
        notebook = conn.execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
        if not notebook: abort(404, "Folder not found")
        ids = [r[0] for r in conn.execute("SELECT id FROM pages WHERE notebook_id = ? ORDER BY position, id", (notebook_id,))]
        documents = [serialise_document(conn, page_id) for page_id in ids]
        conn.close()
        if not any(document["pages"] for document in documents): abort(400, "Cannot export an empty folder")
        return send_file(documents_to_pdf(documents), mimetype="application/pdf",
                         as_attachment=True, download_name=download_name(notebook["title"]))

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(error):
        return jsonify(error="Uploads must be 40 MB or smaller"), 413

    init_db()
    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=int(os.environ.get("PORT", 5000)))
