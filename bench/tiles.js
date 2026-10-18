// Первый замер тайлов (план M1, шаг 11; ADR-0002 — главный риск: 200 rps на 2 ядрах).
//   docker run --rm -i --network <сеть compose> -e BASE=http://api:8000 -e LAYER=<id> \
//     -e MODE=warm|cold grafana/k6 run - < bench/tiles.js
// warm — небольшой набор тайлов z11–z12 по кругу (из дискового кэша после первого прохода);
// cold — случайные тайлы z14–z16 по Москве (почти всегда рендер).
import http from 'k6/http'
import { check } from 'k6'

const BASE = __ENV.BASE || 'http://api:8000'
const TOKEN = __ENV.TOKEN || 'dev-admin-token'
const MODE = __ENV.MODE || 'warm'

export const options = {
  scenarios: {
    tiles: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 200),
      timeUnit: '1s',
      duration: __ENV.DURATION || '30s',
      preAllocatedVUs: 100,
      maxVUs: 400,
    },
  },
  thresholds: { http_req_failed: ['rate<0.01'] },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
}

export function setup() {
  const resp = http.post(`${BASE}/v1/tiles/sign`, JSON.stringify({ layer: __ENV.LAYER, tms: __ENV.TMS || '3857' }), {
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  })
  return { template: resp.json('url_template').replace('http://localhost:8000', BASE) }
}

function tile(lon, lat, z) {
  const n = 2 ** z
  const x = Math.floor(((lon + 180) / 360) * n)
  const y = Math.floor(((1 - Math.asinh(Math.tan((lat * Math.PI) / 180)) / Math.PI) / 2) * n)
  return [z, x, y]
}

export default function (data) {
  let z, x, y
  if (MODE === 'warm') {
    ;[z, x, y] = tile(37.2 + Math.floor(Math.random() * 6) * 0.15, 55.5 + Math.floor(Math.random() * 4) * 0.1, 11 + (__ITER % 2))
  } else {
    ;[z, x, y] = tile(37.2 + Math.random() * 0.9, 55.5 + Math.random() * 0.5, 14 + Math.floor(Math.random() * 3))
  }
  const url = data.template.replace('{z}', z).replace('{x}', x).replace('{y}', y)
  const resp = http.get(url)
  check(resp, { 'png 200': (r) => r.status === 200 })
}
