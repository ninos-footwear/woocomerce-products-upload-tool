"""
"Select images folder" button for the Streamlit app.

The browser opens a folder dialog, reads only the FILE NAMES of everything in the
chosen folder (sub-folders included) and sends that list to Python.
The images themselves are never read or uploaded.
"""
import streamlit as st

_HTML = """
<label class="pick">
  <input type="file" webkitdirectory directory multiple />
  <span>📁 Select images folder</span>
</label>
<span class="hint">Only file names are read. Nothing is uploaded.</span>
"""

_CSS = """
.pick input { display: none; }
.pick span {
  display: inline-block; cursor: pointer; padding: 0.45rem 0.9rem; border-radius: 0.5rem;
  border: 1px solid var(--st-border-color, #ccc); background: var(--st-secondary-background-color, #f0f2f6);
  color: var(--st-text-color, inherit); font-family: var(--st-font, sans-serif); font-size: 0.95rem;
}
.pick span:hover { border-color: var(--st-primary-color, #ff4b4b); color: var(--st-primary-color, #ff4b4b); }
.hint { margin-left: 0.75rem; font-size: 0.8rem; opacity: 0.7; font-family: var(--st-font, sans-serif); }
"""

_JS = """
export default function (component) {
  const { parentElement, setStateValue } = component;
  const input = parentElement.querySelector('input[type=file]');
  if (!input) return;
  input.onchange = () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    const first = files[0].webkitRelativePath || '';
    const folder = first.includes('/') ? first.split('/')[0] : '';
    // names only: File objects are never read
    setStateValue('picked', { folder: folder, names: files.map(f => f.name), at: Date.now() });
    input.value = '';
  };
}
"""

_component = st.components.v2.component("ninos_folder_names_picker", html=_HTML, css=_CSS, js=_JS)


def pick_folder_names(key="images_folder"):
    """Show the button. Returns {'folder': str, 'names': [str, ...]} once a folder was picked, else None."""
    result = _component(key=key, on_picked_change=lambda: None, height="content")
    picked = result.get("picked") if hasattr(result, "get") else getattr(result, "picked", None)
    return picked or None
