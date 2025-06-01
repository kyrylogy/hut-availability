const API_KEY = '__MAPY_API_KEY__'; // Replace with your actual Mapy.cz API key
const AVAILABILITY_ENDPOINT = location.hostname === 'localhost'
  ? 'http://localhost:8888/.netlify/functions/getHutAvailability'
  : '__NETLIFY_FUNCTION_URL__'; // Replace with your actual Netlify function URL

const map = L.map('map').setView([47.5, 13.3], 8);

const tileLayers = {
  'Outdoor': L.tileLayer(`https://api.mapy.cz/v1/maptiles/outdoor/256/{z}/{x}/{y}?apikey=${API_KEY}`, {
    minZoom: 0,
    maxZoom: 19,
    attribution: '<a href="https://api.mapy.cz/copyright" target="_blank">© Seznam.cz a.s.</a>'
  }),
  'Winter': L.tileLayer(`https://api.mapy.cz/v1/maptiles/winter/256/{z}/{x}/{y}?apikey=${API_KEY}`),
  'Aerial': L.tileLayer(`https://api.mapy.cz/v1/maptiles/aerial/256/{z}/{x}/{y}?apikey=${API_KEY}`),
  'Basic': L.tileLayer(`https://api.mapy.cz/v1/maptiles/basic/256/{z}/{x}/{y}?apikey=${API_KEY}`)
};

tileLayers['Outdoor'].addTo(map);
L.control.layers(tileLayers).addTo(map);

const LogoControl = L.Control.extend({
  options: { position: 'bottomleft' },
  onAdd: function () {
    const container = L.DomUtil.create('div');
    const link = L.DomUtil.create('a', '', container);
    link.href = 'https://mapy.cz/';
    link.target = '_blank';
    link.innerHTML = '<img src="https://api.mapy.cz/img/api/logo.svg" />';
    L.DomEvent.disableClickPropagation(link);
    return container;
  }
});
new LogoControl().addTo(map);

// Global calendar data store
const calendarData = {};

function getMonthName(month) {
  return new Date(2000, month - 1, 1).toLocaleString('default', { month: 'long' });
}

function generateCalendar(dataByMonth, activeYear, activeMonth, getMonthNameFn) {
  const monthKey = `${activeYear}-${activeMonth.toString().padStart(2, '0')}`;
  const daysDataForMonth = dataByMonth[monthKey] || [];
  const numDaysInMonth = new Date(activeYear, activeMonth, 0).getDate();

  const firstDayDateObj = new Date(activeYear, activeMonth - 1, 1);
  let startOffset = firstDayDateObj.getDay();
  startOffset = (startOffset === 0) ? 6 : startOffset - 1;

  const currentMonthName = getMonthNameFn(activeMonth);

  let html = `
    <div style="text-align:center; font-weight:bold; margin-bottom:4px; font-size: 14px;">
      ${currentMonthName} ${activeYear}
    </div>
    <table class="popup-calendar">
      <thead>
        <tr><th>M</th><th>D</th><th>M</th><th>D</th><th>F</th><th>S</th><th>S</th></tr>
      </thead>
      <tbody>
  `;

  let dayCounter = 1;
  for (let r = 0; r < 6; r++) {
    if (dayCounter > numDaysInMonth && r > 0) break;
    html += '<tr>';
    for (let c = 0; c < 7; c++) {
      if (r === 0 && c === 0 && startOffset > 0) {
        html += `<td colspan="${startOffset}" class="month-label-cell">${currentMonthName.substring(0,3).toUpperCase()}</td>`;
        c = startOffset - 1;
        continue;
      }

      if ((r === 0 && c < startOffset) || dayCounter > numDaysInMonth) {
        html += '<td></td>';
      } else {
        const dayData = daysDataForMonth[dayCounter - 1];
        let cellClass = 'day-cell';
        let previewHtml = '';

        if (dayData) {
          if (dayData.hutStatus === 'CLOSED' || dayData.freeBeds === null) {
            cellClass += ' day-disabled';
          } else if (typeof dayData.freeBeds === 'number') {
            let previewColorClass = '';
            if (dayData.percentage === 'AVAILABLE') previewColorClass = 'preview-available';
            else if (dayData.percentage === 'NEARLY FULL') previewColorClass = 'preview-nearly-full';
            else if (dayData.percentage === 'FULL') previewColorClass = 'preview-full';
            else if (dayData.freeBeds > 10) previewColorClass = 'preview-available';
            else if (dayData.freeBeds > 0) previewColorClass = 'preview-nearly-full';
            else if (dayData.freeBeds === 0) previewColorClass = 'preview-full';

            if (previewColorClass) {
              previewHtml = `<div class="day-preview ${previewColorClass}">${dayData.freeBeds}</div>`;
            }
          }
        } else {
          cellClass += ' day-no-data';
        }

        html += `<td class="${cellClass}">
                    <div class="day-cell-content">
                        <span class="day-number">${dayCounter}</span>
                        ${previewHtml}
                    </div>
                  </td>`;
        dayCounter++;
      }
    }
    html += '</tr>';
    if (dayCounter > numDaysInMonth) break;
  }

  html += `</tbody></table>`;
  return html;
}

function updateCalendar(hutId, year, month) {
  const calRootId = `cal-root-${hutId}`;
  const dataByMonth = calendarData[hutId];
  if (!dataByMonth) return;
  const html = generateCalendar(dataByMonth, parseInt(year), parseInt(month), getMonthName);
  const root = document.getElementById(calRootId);
  if (root) root.innerHTML = html;
}

fetch('huts_with_coords.json')
  .then(res => res.json())
  .then(huts => {
    huts.forEach(hut => {
        let lat = hut.lat;
        let lng = hut.lng;

        // Fallback to coordinates string if lat/lng are null
        if ((lat === null || lng === null) && hut.coordinates) {
            const coords = hut.coordinates.split(',').map(s => parseFloat(s.trim()));
            if (coords.length === 2 && !isNaN(coords[0]) && !isNaN(coords[1])) {
            lat = coords[0];
            lng = coords[1];
            }
        }

        if (lat === null || lng === null) return;

        const marker = L.marker([lat, lng]).addTo(map);
        marker.bindPopup(`<b>${hut.hutName}</b><br><em>Loading availability...</em>`, { autoPan: true, minWidth: 280, autoPanPadding: [20, 60] });

        marker.on('click', () => {
            fetch(`${AVAILABILITY_ENDPOINT}?hutId=${hut.hutId}`)
            .then(res => res.json())
            .then(data => {
                const dataByMonth = {};

                if (!Array.isArray(data)) throw new Error("Invalid data format");

                data.forEach(day => {
                const [d, m, y] = day.dateFormatted.split('.').map(Number);
                const key = `${y}-${String(m).padStart(2, '0')}`;
                if (!dataByMonth[key]) {
                    const daysInMonth = new Date(y, m, 0).getDate();
                    dataByMonth[key] = new Array(daysInMonth).fill(null);
                }
                dataByMonth[key][d - 1] = day;
                });

                calendarData[hut.hutId] = dataByMonth;
                const sortedMonths = Object.keys(dataByMonth).sort();

                let initialYear = 0, initialMonth = 0;
                if (sortedMonths.length > 0) {
                [initialYear, initialMonth] = sortedMonths[0].split('-').map(Number);
                }

                const calRootId = `cal-root-${hut.hutId}`;
                const monthButtons = sortedMonths.map(key => {
                const [y, m] = key.split('-');
                return `<button class="month-switcher-btn" onclick="updateCalendar('${hut.hutId}', '${y}', '${m}')">${getMonthName(parseInt(m)).substring(0, 3)} '${y.slice(2)}</button>`;
                }).join(' ');

                const calendarHtml = (initialYear && initialMonth)
                ? generateCalendar(dataByMonth, initialYear, initialMonth, getMonthName)
                : `<em>No availability data to display.</em>`;

                const pictureHtml = hut.picture?.blobPath
                ? `<img src="${hut.picture.blobPath}" alt="${hut.hutName}" style="width:100%;max-height:160px;object-fit:cover;margin-bottom:4px;border-radius:4px;" />`
                : '';

                const elevationHtml = hut.altitude ? `<div style="margin:4px 0;"><strong>Elevation:</strong> ${hut.altitude}</div>` : '';


                const popupHtml = `
                <div class="hut-popup-container">
                    ${pictureHtml}
                    <div style="font-weight: bold; margin: 4px 0;">${hut.hutName}</div>
                    ${elevationHtml}
                    <a href="https://www.hut-reservation.org/reservation/book-hut/${hut.hutId}/wizard" target="_blank" class="booking-link" style="display: inline-block; margin-bottom: 6px;">📅 Book this hut</a>
                    <div style="margin: 5px 0; display:flex; flex-wrap:wrap; gap:4px; justify-content:center;">${monthButtons}</div>
                    <div id="${calRootId}">${calendarHtml}</div>
                </div>
                <style>
                    .popup-calendar { border-collapse: collapse; margin: 10px auto 5px auto; font-family: Arial, sans-serif; }
                    .popup-calendar th, .popup-calendar td { width: 30px; height: 34px; text-align: center; vertical-align: top; border: 1px solid #ddd; box-sizing: border-box; }
                    .popup-calendar th { font-weight: bold; font-size: 10px; padding: 3px 0; color: #333; background-color: #f9f9f9; }
                    .month-label-cell { font-weight: bold; font-size: 11px; color: #555; background-color: #f0f0f0; vertical-align: middle !important; }
                    .day-cell-content { display: flex; flex-direction: column; align-items: center; justify-content: flex-start; padding-top: 2px; height: 100%; }
                    .day-number { font-size: 12px; color: #333; }
                    .day-preview { font-size: 10px; font-weight: bold; margin-top: 1px; }
                    .day-disabled .day-number, .day-no-data .day-number { color: #aaa; }
                    .day-disabled .day-cell-content, .day-no-data .day-cell-content { background-color: #f5f5f5; }
                    .day-disabled .day-preview, .day-no-data .day-preview { display: none; }
                    .preview-available { color: green; }
                    .preview-nearly-full { color: orange; }
                    .preview-full { color: red; }
                    .month-switcher-btn { padding: 3px 6px; margin: 2px; border: 1px solid #ccc; border-radius: 3px; background-color: #f0f0f0; cursor: pointer; font-size: 11px; }
                    .month-switcher-btn:hover { background-color: #e0e0e0; }
                    .hut-popup-container img { width: 100%; max-height: 160px; object-fit: cover; margin-bottom: 4px; border-radius: 4px; }
                </style>
                `;

                marker.getPopup().setContent(popupHtml).update();
            })
            .catch(err => {
                console.error(`Error loading availability for ${hut.hutName}:`, err);
                marker.getPopup().setContent(`<b>${hut.hutName}</b><br><em>Error loading availability data.</em>`).update();
            });
        });
    });
})
    
  .catch(err => {
    console.error("Failed to load huts_with_coords.json:", err);
  });
