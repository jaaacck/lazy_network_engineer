# Changelog

All notable changes to Lazy Network Engineer will be documented in this file.

## [1.2.6] - 2026-01-24

### Fixed
- Fixed URL pattern ordering for uploaded images - pattern now inserted at beginning of urlpatterns
- Ensured uploaded images are served before Django's default static file handler
- Images should now be accessible at /static/uploads/ URLs

## [1.2.5] - 2026-01-24

### Fixed
- Fixed static file serving for uploaded images in development mode
- Images are now properly accessible at /static/uploads/ URLs
- Changed from static() helper to re_path with serve view for better control

## [1.2.4] - 2026-01-24

### Fixed
- Added CSS styling for images in markdown content to ensure proper display
- Images now have max-width, proper margins, and visual styling

## [1.2.3] - 2026-01-24

### Fixed
- Images in activity stream updates are now properly displayed
- Added img tag to allowed HTML tags in markdown sanitization

## [1.2.2] - 2026-01-24

### Added
- Clipboard image paste support (Ctrl+V) in activity update forms for tasks and subtasks
- Images are automatically uploaded and inserted as markdown when pasted
- Image upload endpoint at /api/upload-image/
- Images stored in data/uploads/YYYY/MM/ directory structure

### Changed
- Static files configuration updated to serve uploaded images
- Removed apostrophes from all comments in code files

## [1.2.1] - 2026-01-24

### Changed
- Removed all external CDN dependencies
- EasyMDE markdown editor now loaded from local static files instead of jsDelivr CDN

### Added
- Local copies of EasyMDE CSS and JavaScript files in `pm/static/pm/css/` and `pm/static/pm/js/`

### Removed
- External CDN links to jsDelivr for EasyMDE assets

## [1.2.0] - 2026-01-24

### Added
- Basic test suite with validation, functionality, and URL tests
- Test database initialization for search index tables

### Changed
- Improved code organization: moved color/label utility functions from views.py to utils.py
- Better separation of concerns (views focus on request handling, utils on data manipulation)

### Fixed
- Fixed incomplete code in `task_detail` view (subtask_title assignment)
- Fixed incomplete code in `people_list` view (load_person call)
- Fixed incomplete code in `person_detail` view (total_refs calculation)
- Fixed syntax error in subtask creation (duplicate assignment)

### Removed
- Deleted unused static files: bootstrap.min.js, bootstrap.min.css, jquery.min.js, marked.min.js, markdown-preview.js (~290KB removed)
- Removed unused hashlib import from views.py (moved to utils.py where needed)

### Code Quality
- Added comprehensive test coverage for core functionality
- Fixed syntax errors and incomplete code
- Removed dead code and unused dependencies
- Improved code maintainability through better organization
- Reduced views.py complexity by moving utility functions to utils.py

## [1.1.0] - 2026-01-24

### Added
- Version number and changelog system in help page
- Clickable changelog link below version number

### Changed
- Moved mode-toggle buttons (view/edit/kanban/archive) to top right, inline with page titles
- Standardized button theming across all pages (Archive, Unarchive, Move, Delete buttons now match mode-toggle style)
- Moved update form (textarea + Post Update button) to top of Activity section in tasks, epics, and subtasks
- Fixed template syntax error in epic_detail.html (missing task-overview div opening tag)

### Fixed
- Template syntax error causing epic detail page to fail
- Inconsistent button styling (Archive button now matches theme)
- Update form positioning (now at top of activity section for better UX)

### Theming
- Removed inline styles from templates and moved to CSS classes
- Fixed people-chip colors to use dynamic colors instead of hardcoded blue
- Standardized spacing, fonts, colors, and alignment across all pages
- Consistent border-radius (3px) throughout application
- Standardized button styles and hover states
