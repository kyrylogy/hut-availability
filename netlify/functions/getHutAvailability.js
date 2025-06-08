// netlify/functions/getHutAvailability.js
// No need to import fetch in modern Node.js (18+)

exports.handler = async (event) => {
  const hutId = event.queryStringParameters?.hutId;
  if (!hutId) {
    return {
      statusCode: 400,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ error: "Missing hutId" }),
    };
  }

  const url = `https://www.hut-reservation.org/api/v1/reservation/getHutAvailability?hutId=${hutId}`;

  try {
    console.log(`Fetching data for hut ${hutId} from: ${url}`);
    
    const response = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9"
      },
    });

    console.log(`Response status: ${response.status}`);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    console.log(`Successfully fetched data for hut ${hutId}, entries: ${data.length}`);

    return {
      statusCode: 200,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(data),
    };

  } catch (err) {
    console.error(`Error fetching data for hut ${hutId}:`, err.message);
    
    return {
      statusCode: 500,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ error: err.message }),
    };
  }
};