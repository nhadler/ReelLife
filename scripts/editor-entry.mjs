// The notes editor's parts, bundled into cues/static/cues/vendor/tiptap.min.js
// by scripts/vendor_assets.py (esbuild). Only what notes-panel.js uses.
export {Editor} from '@tiptap/core';
export {StarterKit} from '@tiptap/starter-kit';
export {Placeholder} from '@tiptap/extensions';
export {TaskItem, TaskList} from '@tiptap/extension-list';
export {Markdown} from '@tiptap/markdown';
// The editor gets its own parser with HTML turned off (notes-panel.js).
export {Marked} from 'marked';
