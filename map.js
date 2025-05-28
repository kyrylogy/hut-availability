
const API_KEY = '__MAPY_API_KEY__';

const map = L.map('map').setView([47.5, 13.3], 8);

const tileLayers = {
  'Outdoor': L.tileLayer(`https://api.mapy.cz/v1/maptiles/outdoor/256/{z}/{x}/{y}?apikey=${API_KEY}`, {
    minZoom: 0,
    maxZoom: 19,
    attribution: '<a href="https://api.mapy.cz/copyright" target="_blank">&copy; Seznam.cz a.s.</a>'
  }),
  'Winter': L.tileLayer(`https://api.mapy.cz/v1/maptiles/winter/256/{z}/{x}/{y}?apikey=${API_KEY}`),
  'Aerial': L.tileLayer(`https://api.mapy.cz/v1/maptiles/aerial/256/{z}/{x}/{y}?apikey=${API_KEY}`),
  'Basic': L.tileLayer(`https://api.mapy.cz/v1/maptiles/basic/256/{z}/{x}/{y}?apikey=${API_KEY}`)
};

tileLayers['Outdoor'].addTo(map);
L.control.layers(tileLayers).addTo(map);

// Add Mapy.cz logo control
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

// Load huts and create markers
fetch('huts_with_coords.json')
  .then(res => res.json())
  .then(huts => {
    huts.forEach(hut => {
      if (!hut.lat || !hut.lng) return;

      const marker = L.marker([hut.lat, hut.lng]).addTo(map);
      marker.bindPopup(`<b>${hut.hutName}</b><br><em>Loading availability...</em>`, { autoPan: false });

      marker.on('click', () => {
        fetch(`availability/${hut.hutId}.json`)
          .then(res => res.json())
          .then(data => {
            let html = `<b>${hut.hutName}</b><br><div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:5px;">`;
            data.forEach(day => {
              const beds = day.freeBeds;
              const date = new Date(day.dateFormatted).getDate();
              let color = beds > 30 ? 'green' : beds > 10 ? 'orange' : 'red';
              html += `
                <div style="
                  background:${color}; 
                  color:white; 
                  width:32px; 
                  height:32px; 
                  border-radius:50%; 
                  display:flex; 
                  align-items:center; 
                  justify-content:center; 
                  font-size:12px;
                  font-weight:bold;
                " title="${day.dateFormatted}">
                  ${date}
                </div>`;
            });
            html += '</div>';
            marker.getPopup().setContent(html).update();
          })
          .catch(() => {
            marker.getPopup().setContent(`<b>${hut.hutName}</b><br><em>No availability data</em>`).update();
          });
      });
    });
  });
