# 🏔️ Hut Availability Map

This project visualizes mountain huts (e.g. in Austria) on an interactive map (Mapy.cz) with availability displayed in calendar form for each hut.

---

## 🚀 Features

- Map tiles from **Mapy.cz Outdoor** using API key
- Markers for each hut (from `huts_with_coords.json`)
- Popup calendar with availability fetched from the hut-reservation.org API
- **Month switcher** to view beds for different months
- Deployed via **GitHub Pages**
- Uses a **Netlify Function** as a CORS-safe proxy to fetch live availability data

---

## 🔧 Why netlify?

The original API at `www.hut-reservation.org` does not support CORS for browser-side fetch.  
We use **Netlify Functions** to act as a secure, serverless **proxy API**:

```yaml
GET /.netlify/functions/getHutAvailability?hutId=123
```

This avoids CORS issues and hides API request headers (like XSRF tokens, if any).

---

## 🛠️ Installation (Local Dev)

1. **Clone the repo**:

```bash
git clone https://github.com/your-username/hut-availability.git
cd hut-availability
```

2. **Install netlify cli (if not already)**:
```bash
npm install -g netlify-cli
```
3. **(Optional) Add your Mapy.cz API key**:
You can get them at https://developer.mapy.com/.
Replace the key in the beginning of **map.js** file

4. **Run locally**:
```bash
netlify dev
```


## Project structure
```
.
├── index.html                # Main entrypoint
├── map.js                    # Map + calendar logic (API placeholder injected at build)
├── huts_with_coords.json     # Static hut coordinates and names
├── netlify/functions/
│   └── getHutAvailability.js # Serverless function to fetch hut data by hutId
├── .env                      # (local only) Holds API secrets
```

