# Document Annotator

A local, single-user Flask app for marking up documents. Upload a PDF or an image, and it renders
as a scrolling column of pages you annotate directly — pen, highlighter, text, sticky notes, and
image stamps — then export the result as a flattened PDF.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open `http://127.0.0.1:5000`. The app creates `whiteboard.db` and an `uploads/` directory beside
`app.py`; both are local to this project.

## Use

- **Folders** hold documents. Create one from the sidebar, then click **Upload PDF or image** (or
  drag a file onto it). PDFs are rendered at 150 DPI, one page per page. **+ Blank** starts an
  empty letter-sized document instead.
- **Select** moves and resizes annotations; double-click a note or text box to edit it. **Pen** and
  **Highlighter** draw with mouse, touch, or stylus. **Text** and **Sticky note** drop an annotation
  where you click. **Eraser** removes a stroke you click on.
- **📷** stamps an image (a signature, say) onto the page you are viewing. **+ Page** appends a
  blank page to the document.
- Zoom with the toolbar buttons or `Ctrl`/`Cmd` + wheel; **↔** fits the page width.
- **⇩ PDF** exports the open document with every annotation flattened into it. The **↓** button on
  a folder exports all of its documents as one PDF.
- `Ctrl+Z` / `Ctrl+Shift+Z` undo and redo. `Delete` removes the selected annotation.

## How annotations are stored

Each annotation belongs to one page of one document (`doc_pages`), and its coordinates are in that
page's own PDF point space. Zooming, resizing the window, and exporting all reuse those same
coordinates, so a highlight stays on the words it was drawn over.

Boards created by the earlier infinite-whiteboard version are migrated on first run: each becomes a
single blank page sized to fit its content, with all of its notes and strokes preserved.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests -q
```
