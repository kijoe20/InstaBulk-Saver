# Instagram Bulk Downloader (Local)

A fully local Streamlit app to preview and bulk-download images (and videos) from Instagram post URLs. No cloud APIs or keys required.

## Features
- Paste multiple Instagram post URLs (comma or newline separated)
- Preview all images (including carousel posts) and videos
- Select media to download, with Select All / Download All options
- Saves at highest available quality into `downloads/<shortcode>/` folders
- Skips existing files to avoid duplicates
- Optional: Load an Instaloader session file for private posts
- Progress bars, logs, and clear three-step UI

## Requirements
- Python 3.9+

## Installation
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
streamlit run app.py
```

## Usage
1. (Optional) In the sidebar, provide your Instagram username and upload an Instaloader session file if you need to access private posts you follow.
2. Paste Instagram post URLs into the text area (separated by commas or new lines).
3. Click "Preview" to fetch thumbnails for all media (no downloads yet).
4. Select desired images/videos and click "Download Selected" (or use "Select All" / "Download All").
5. Files are saved under `downloads/<shortcode>/` with duplicates skipped.

## Notes
- The app spaces requests to respect Instagram rate limits. If you see errors, try fewer URLs or wait and retry.
- Session File: Create with Instaloader (see docs) and upload the resulting `.session` file. Enter the same username used to create it.
- Everything runs locally. No data or keys are sent to external services.

## Troubleshooting
- If previews fail, ensure the URLs are direct post URLs like:
  - `https://www.instagram.com/p/SHORTCODE/`
  - `https://www.instagram.com/reel/SHORTCODE/`
  - `https://www.instagram.com/tv/SHORTCODE/`
- Private or restricted posts may require a valid session file and the correct username.
- Some corporate networks or VPNs may block CDN access to images/videos.

## License
MIT
