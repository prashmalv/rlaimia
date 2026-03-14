# Font Setup for Mia Campaign Engine

## Required Fonts

### 1. Gotham (Headings & Sub-headings)
- **Usage**: All heading and sub-heading text on images and videos
- **License**: Commercial — licensed by Hoefler & Co (https://www.typography.com/fonts/gotham/overview)
- **Files needed**:
  ```
  assets/fonts/gotham/Gotham-Bold.otf       ← Main heading
  assets/fonts/gotham/Gotham-Medium.otf     ← Sub-heading
  assets/fonts/gotham/Gotham-Book.otf       ← CTA text
  ```
- **Action**: Place your licensed Gotham font files in `assets/fonts/gotham/`

### 2. EB Garamond (Body text)
- **Usage**: Message body, personalized offer text
- **License**: Open source (SIL Open Font License)
- **Download**: https://fonts.google.com/specimen/EB+Garamond
- **OR via npm**: The Dockerfile auto-downloads these. For local dev:
  ```
  assets/fonts/garamond/EBGaramond-Regular.ttf
  assets/fonts/garamond/EBGaramond-Bold.ttf
  assets/fonts/garamond/EBGaramond-Italic.ttf
  ```
- **Action**: Download from Google Fonts → EB Garamond

## Fallback Behavior
If Gotham files are not found, the system falls back to:
1. EB Garamond (if available)
2. System default font

Configure custom paths via environment variables:
```env
FONT_GOTHAM_BOLD=/path/to/Gotham-Bold.otf
FONT_GOTHAM_MEDIUM=/path/to/Gotham-Medium.otf
FONT_GARAMOND_REGULAR=/path/to/EBGaramond-Regular.ttf
```
