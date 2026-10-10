# Playground console

The console gives cameras and task chat the first screen. Four-view YAM video uses a large observer and three smaller detail views. A wide, shallow window stacks the details beside the observer; phones place cameras, status, and queue above task chat. Conversation history scrolls inside the desktop panel.

The compact composer mirrors the existing provider picker. Plus opens run setup, and Send uses the existing submission handler. Missing required fields open setup first. While active, Send becomes Stop or Leave queue. The heading includes the active prompt. Visitors opens a closable right-side panel.

Past runs use a thumbnail grid with exclusive pointer or keyboard previews, followed by dataset access and a short guide. Four-camera recording frames are recomposed for display without changing stored files. Reduced-motion preferences disable automatic previews.

YAM's default live path is `synchronized-hd`: one native 1920×1080 observer and three 640×360 details in a 2560×1080 atlas. Explicit `YAM_VIDEO_STREAMS` overrides remain supported, including `synchronized`. The viewer decodes both formats and validates the optional capture timestamp before showing approximate video age. Other robots retain their existing stream configuration.

Microphone input and image attachments require separate functionality. The composer currently exposes working task, model, settings, and stop controls.
