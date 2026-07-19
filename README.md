# Notes Whiteboard

A local, single-user Flask whiteboard for organizing subject notebooks and freeform note pages.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser. The application creates `whiteboard.db` and an `uploads/` directory beside `app.py`; both are local to this project.

## Use

- Create notebooks and pages from the left sidebar; drag entries to reorder them.
- Use **Select** to pan the board and move or resize notes/images.
- Use **Pen** or **Highlighter** to draw with mouse, touch, or stylus. **Eraser** removes a selected stroke.
- Hold `Ctrl` (or `Cmd`) while using the mouse wheel/trackpad to zoom.
