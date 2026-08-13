import io
import json
import os
import sqlite3
import subprocess
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=str(BASE_DIR / "whiteboard.db"),
        UPLOAD_FOLDER=str(UPLOAD_DIR),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    def connect():
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

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
                title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS board_items (
                id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('note','image')), content TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#fff3a8', x REAL NOT NULL DEFAULT 120, y REAL NOT NULL DEFAULT 100,
                width REAL NOT NULL DEFAULT 260, height REAL NOT NULL DEFAULT 180, position INTEGER NOT NULL DEFAULT 0,
                image_name TEXT
            );
            CREATE TABLE IF NOT EXISTS strokes (
                id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                tool TEXT NOT NULL CHECK(tool IN ('pen','highlighter')), color TEXT NOT NULL,
                width REAL NOT NULL, points TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

    def payload():
        return request.get_json(silent=True) or {}

    def get_page(conn, page_id):
        page = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        if not page:
            abort(404, "Page not found")
        return page

    def serialise_page(conn, page_id):
        page = dict(get_page(conn, page_id))
        page["items"] = rows(conn, "SELECT * FROM board_items WHERE page_id = ? ORDER BY position, id", (page_id,))
        page["strokes"] = rows(conn, "SELECT * FROM strokes WHERE page_id = ? ORDER BY position, id", (page_id,))
        for stroke in page["strokes"]:
            stroke["points"] = json.loads(stroke["points"])
        return page

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/notebooks")
    def list_notebooks():
        conn = connect()
        notebooks = rows(conn, "SELECT * FROM notebooks ORDER BY position, id")
        for notebook in notebooks:
            notebook["pages"] = rows(conn, "SELECT * FROM pages WHERE notebook_id = ? ORDER BY position, id", (notebook["id"],))
        conn.close()
        return jsonify(notebooks)

    @app.post("/api/notebooks")
    def create_notebook():
        title = str(payload().get("title", "Untitled notebook")).strip()[:100] or "Untitled notebook"
        conn = connect()
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM notebooks").fetchone()[0]
        cursor = conn.execute("INSERT INTO notebooks(title, position) VALUES (?, ?)", (title, pos))
        conn.commit(); notebook = dict(conn.execute("SELECT * FROM notebooks WHERE id = ?", (cursor.lastrowid,)).fetchone()); conn.close()
        return jsonify(notebook), 201

    @app.patch("/api/notebooks/<int:notebook_id>")
    def update_notebook(notebook_id):
        title = str(payload().get("title", "")).strip()[:100]
        if not title: abort(400, "A notebook title is required")
        conn = connect(); cursor = conn.execute("UPDATE notebooks SET title = ? WHERE id = ?", (title, notebook_id)); conn.commit()
        if not cursor.rowcount: abort(404, "Notebook not found")
        conn.close(); return jsonify({"id": notebook_id, "title": title})

    @app.delete("/api/notebooks/<int:notebook_id>")
    def delete_notebook(notebook_id):
        conn = connect()
        names = [r[0] for r in conn.execute("SELECT image_name FROM board_items WHERE image_name IS NOT NULL AND page_id IN (SELECT id FROM pages WHERE notebook_id = ?)", (notebook_id,))]
        cursor = conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,)); conn.commit(); conn.close()
        if not cursor.rowcount: abort(404, "Notebook not found")
        for name in names:
            (Path(app.config["UPLOAD_FOLDER"]) / name).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/notebooks/<int:notebook_id>/pages")
    def create_page(notebook_id):
        title = str(payload().get("title", "Untitled page")).strip()[:100] or "Untitled page"
        conn = connect()
        if not conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone(): abort(404, "Notebook not found")
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pages WHERE notebook_id = ?", (notebook_id,)).fetchone()[0]
        cursor = conn.execute("INSERT INTO pages(notebook_id, title, position) VALUES (?, ?, ?)", (notebook_id, title, pos)); conn.commit()
        page = dict(conn.execute("SELECT * FROM pages WHERE id = ?", (cursor.lastrowid,)).fetchone()); conn.close(); return jsonify(page), 201

    @app.patch("/api/pages/<int:page_id>")
    def update_page(page_id):
        title = str(payload().get("title", "")).strip()[:100]
        if not title: abort(400, "A page title is required")
        conn = connect(); cursor = conn.execute("UPDATE pages SET title = ? WHERE id = ?", (title, page_id)); conn.commit()
        if not cursor.rowcount: abort(404, "Page not found")
        conn.close(); return jsonify({"id": page_id, "title": title})

    @app.delete("/api/pages/<int:page_id>")
    def delete_page(page_id):
        conn = connect(); names = [r[0] for r in conn.execute("SELECT image_name FROM board_items WHERE page_id = ? AND image_name IS NOT NULL", (page_id,))]
        cursor = conn.execute("DELETE FROM pages WHERE id = ?", (page_id,)); conn.commit(); conn.close()
        if not cursor.rowcount: abort(404, "Page not found")
        for name in names: (Path(app.config["UPLOAD_FOLDER"]) / name).unlink(missing_ok=True)
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
        conn = connect(); result = serialise_page(conn, page_id); conn.close(); return jsonify(result)

    @app.post("/api/pages/<int:page_id>/items")
    def create_item(page_id):
        data = payload(); kind = data.get("kind", "note")
        if kind != "note": abort(400, "Use the upload endpoint for images")
        conn = connect(); get_page(conn, page_id)
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM board_items WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("INSERT INTO board_items(page_id, kind, content, color, x, y, width, height, position) VALUES (?, 'note', ?, ?, ?, ?, ?, ?, ?)",
            (page_id, str(data.get("content", "")).strip(), data.get("color", "#fff3a8"), data.get("x", 140), data.get("y", 120), data.get("width", 260), data.get("height", 180), pos))
        conn.commit(); item = dict(conn.execute("SELECT * FROM board_items WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close(); return jsonify(item), 201

    @app.patch("/api/items/<int:item_id>")
    def update_item(item_id):
        data = payload(); allowed = {"content", "color", "x", "y", "width", "height", "position"}
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
        if item["image_name"]: (Path(app.config["UPLOAD_FOLDER"]) / item["image_name"]).unlink(missing_ok=True)
        return "", 204

    @app.post("/api/pages/<int:page_id>/upload")
    def upload_image(page_id):
        file = request.files.get("image")
        if not file or not file.filename: abort(400, "Choose an image to upload")
        ext = Path(secure_filename(file.filename)).suffix.lower().lstrip(".")
        if ext not in ALLOWED_EXTENSIONS or file.mimetype not in ALLOWED_MIMES: abort(400, "Only PNG, JPEG, GIF, and WebP images are allowed")
        conn = connect(); get_page(conn, page_id)
        image_name = f"{uuid.uuid4().hex}.{ext}"; file.save(Path(app.config["UPLOAD_FOLDER"]) / image_name)
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM board_items WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("INSERT INTO board_items(page_id, kind, content, x, y, width, height, position, image_name) VALUES (?, 'image', '', 180, 160, 320, 240, ?, ?)", (page_id, pos, image_name))
        conn.commit(); item = dict(conn.execute("SELECT * FROM board_items WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close(); return jsonify(item), 201

    @app.get("/uploads/<path:filename>")
    def uploads(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.post("/api/pages/<int:page_id>/strokes")
    def create_stroke(page_id):
        data = payload(); points = data.get("points", [])
        if data.get("tool") not in {"pen", "highlighter"} or not isinstance(points, list) or len(points) < 2: abort(400, "A valid stroke requires at least two points")
        conn = connect(); get_page(conn, page_id); pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM strokes WHERE page_id = ?", (page_id,)).fetchone()[0]
        cur = conn.execute("INSERT INTO strokes(page_id, tool, color, width, points, position) VALUES (?, ?, ?, ?, ?, ?)", (page_id, data["tool"], data.get("color", "#263238"), max(1, min(float(data.get("width", 4)), 30)), json.dumps(points), pos))
        conn.commit(); stroke = dict(conn.execute("SELECT * FROM strokes WHERE id = ?", (cur.lastrowid,)).fetchone()); conn.close(); stroke["points"] = json.loads(stroke["points"]); return jsonify(stroke), 201

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
            subprocess.run(["git", "add", "whiteboard.db", "uploads/"], cwd=cwd, capture_output=True, check=True)
            result = subprocess.run(["git", "commit", "-m", "Auto-sync: Update database and images"], cwd=cwd, capture_output=True, text=True)
            if result.returncode == 0 or "nothing to commit" in result.stdout.lower() or "nothing to commit" in result.stderr.lower():
                push = subprocess.run(["git", "push", "origin", "main"], cwd=cwd, capture_output=True, text=True)
                if push.returncode == 0: return jsonify({"status": "success", "message": "Changes pushed to GitHub"}), 200
                else: return jsonify({"status": "error", "message": push.stderr or push.stdout}), 400
            else: return jsonify({"status": "error", "message": result.stderr or result.stdout}), 400
        except subprocess.CalledProcessError as e:
            return jsonify({"status": "error", "message": e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.get("/api/notebooks/<int:notebook_id>/export")
    def export_notebook(notebook_id):
        conn = connect()
        notebook = conn.execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
        if not notebook: abort(404, "Notebook not found")
        pages = rows(conn, "SELECT * FROM pages WHERE notebook_id = ? ORDER BY position, id", (notebook_id,))
        if not pages: abort(400, "Cannot export empty notebook")

        pdf_buffer = io.BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=letter)
        width, height = letter

        for page_num, page in enumerate(pages, 1):
            items = rows(conn, "SELECT * FROM board_items WHERE page_id = ? ORDER BY position, id", (page['id'],))
            strokes = rows(conn, "SELECT * FROM strokes WHERE page_id = ? ORDER BY position, id", (page['id'],))

            for stroke in strokes: stroke["points"] = json.loads(stroke["points"])

            # Calculate bounding box
            min_x, min_y, max_x, max_y = float('inf'), float('inf'), 0, 0
            for item in items:
                min_x = min(min_x, item['x']); min_y = min(min_y, item['y'])
                max_x = max(max_x, item['x'] + item['width']); max_y = max(max_y, item['y'] + item['height'])
            for stroke in strokes:
                for p in stroke['points']:
                    min_x = min(min_x, p['x'] - 20); min_y = min(min_y, p['y'] - 20)
                    max_x = max(max_x, p['x'] + 20); max_y = max(max_y, p['y'] + 20)

            if min_x == float('inf'): min_x, min_y, max_x, max_y = 0, 0, 800, 600

            # Render page to image
            img_w, img_h = int(max_x - min_x + 40), int(max_y - min_y + 40)
            img = Image.new('RGB', (img_w, img_h), 'white')
            draw = ImageDraw.Draw(img)

            # Draw strokes
            for stroke in strokes:
                color = stroke['color']
                width = int(stroke['width'])
                points = [(int(p['x'] - min_x + 20), int(p['y'] - min_y + 20)) for p in stroke['points']]
                if len(points) > 1:
                    draw.line(points, fill=color, width=width)

            # Draw items
            for item in items:
                if item['kind'] == 'image' and item['image_name']:
                    try:
                        img_path = Path(app.config["UPLOAD_FOLDER"]) / item['image_name']
                        if img_path.exists():
                            upload_img = Image.open(img_path).resize((int(item['width']), int(item['height'])))
                            img.paste(upload_img, (int(item['x'] - min_x + 20), int(item['y'] - min_y + 20)))
                    except: pass
                elif item['kind'] == 'note' and item['content']:
                    try:
                        font = ImageFont.load_default()
                        draw.text((int(item['x'] - min_x + 25), int(item['y'] - min_y + 25)), item['content'][:100], fill='black', font=font)
                    except: pass

            # Save image temporarily
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)

            # Add to PDF
            c.drawString(0.5 * inch, height - 0.4 * inch, f"Page {page_num}: {page['title']}")
            img_buffer.seek(0)
            from reportlab.platypus import Image as RLImage
            rl_img = RLImage(img_buffer, width=7*inch, height=5*inch)
            rl_img.drawOn(c, 0.4*inch, 1.2*inch)
            c.drawString(width - 1.2*inch, 0.3*inch, f"Page {page_num}")
            c.showPage()

        conn.close()
        c.save()
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name=f"{notebook['title']}.pdf")

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(error):
        return jsonify(error="Image files must be 10 MB or smaller"), 413

    init_db()
    return app


if __name__ == "__main__":
    create_app().run(debug=True)
