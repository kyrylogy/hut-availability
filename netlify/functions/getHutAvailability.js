// netlify/functions/getHutAvailability.js
const fetch = require('node-fetch');

exports.handler = async (event) => {
  const hutId = event.queryStringParameters.hutId;
  if (!hutId) {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: "Missing hutId" }),
    };
  }

  const url = `https://www.hut-reservation.org/api/v1/reservation/getHutAvailability?hutId=${hutId}`;

  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*"
      },
    });

    if (!response.ok) {
      throw new Error(`Error: ${response.status}`);
    }

    const data = await response.json();

    return {
      statusCode: 200,
      headers: {
        "Access-Control-Allow-Origin": "*", // allow frontend use
        "Content-Type": "application/json"
      },
      body: JSON.stringify(data),
    };

  } catch (err) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: err.message }),
    };
  }
};