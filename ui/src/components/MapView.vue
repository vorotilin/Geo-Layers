<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import { formatValue, get, post } from '../api.js'

const props = defineProps({ selection: { type: Object, default: null } })
const container = ref(null)
const opacity = ref(0.8)
const error = ref('')
let map = null
let popup = null

// Подложка OSM (бесплатная, с атрибуцией). MapLibre рисует только EPSG:3857,
// тайлы EPSG:3395 (Яндекс.Карты) проверяются отдельно — scripts/e2e.py.
const style = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© участники OpenStreetMap',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

function removeOverlay() {
  if (!map) return
  if (map.getLayer('data')) map.removeLayer('data')
  if (map.getSource('data')) map.removeSource('data')
}

async function showSelection(sel) {
  error.value = ''
  popup?.remove()
  removeOverlay()
  if (!sel || !map) return
  try {
    const signed = await post('/v1/tiles/sign', {
      layer: sel.layer.id,
      version: sel.version.id,
      tms: '3857',
    })
    if (props.selection !== sel) return // пока подписывали, выбрали другое
    map.addSource('data', { type: 'raster', tiles: [signed.url_template], tileSize: 256 })
    map.addLayer({
      id: 'data',
      type: 'raster',
      source: 'data',
      paint: { 'raster-opacity': opacity.value },
    })
  } catch (e) {
    error.value = e.message
  }
}

async function onClick(e) {
  const sel = props.selection
  if (!sel) return
  const { lng, lat } = e.lngLat
  try {
    const layers = `${sel.layer.id}@${sel.version.id}`
    const resp = await get(`/v1/values?lat=${lat}&lng=${lng}&layers=${layers}`)
    const v = resp.values[0]
    const unit = v.value === null ? '' : sel.layer.unit || ''
    popup?.remove()
    popup = new maplibregl.Popup({ closeButton: false })
      .setLngLat(e.lngLat)
      .setText(`${sel.layer.title}: ${formatValue(v.value)} ${unit}`)
      .addTo(map)
  } catch (err) {
    error.value = err.message
  }
}

onMounted(() => {
  map = new maplibregl.Map({ container: container.value, style, center: [37.62, 55.75], zoom: 8 })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }))
  map.on('click', onClick)
  map.on('load', () => showSelection(props.selection))
})

onBeforeUnmount(() => map?.remove())

watch(
  () => props.selection,
  (sel) => {
    if (map?.loaded()) showSelection(sel)
  },
)
watch(opacity, (v) => {
  if (map?.getLayer('data')) map.setPaintProperty('data', 'raster-opacity', v)
})
</script>

<template>
  <div ref="container" class="map-canvas"></div>
  <div class="map-controls">
    <label>
      Прозрачность слоя
      <input v-model.number="opacity" type="range" min="0" max="1" step="0.05" />
    </label>
    <span v-if="!selection" class="muted">Выберите слой и версию слева. Клик по карте — значение.</span>
    <span v-if="error" class="error">{{ error }}</span>
  </div>
</template>
