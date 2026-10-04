# Camera expansion references

MDN snapshot retrieved 2026-10-04; exact source URLs are in each file.

- `request-fullscreen.md`: arbitrary-element support is limited; request from a
  user gesture, handle promise rejection, and leave native fullscreen via
  `document.exitFullscreen`. Do not request fullscreen on a dialog element.
- `show-modal.md`: `showModal()` places a dialog in the top layer, makes the
  background inert and handles Escape. Use a visible Close control too, restore
  the original camera element on close, and preserve focus when returning.

Small layouts use the dialog directly; desktop retains native fullscreen and
falls back to the dialog if unavailable or denied. Moving the existing canvas
preserves its live painting and avoids another decoder or frame-copy loop.
