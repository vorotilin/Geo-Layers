<script setup>
import { ref } from 'vue'
import { api, formatValue, post } from '../api.js'

const emit = defineEmits(['imported'])
const file = ref(null)
const title = ref('')
const lon = ref('lng')
const lat = ref('lat')
const weight = ref('')
const category = ref('')
const buildLayer = ref(false)
const method = ref('bin')
const step = ref(500)
const bandwidth = ref(500)
const slug = ref('')
const layerTitle = ref('')
const error = ref('')
const busy = ref(false)
const result = ref(null)

function slugify(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
}

async function submit() {
  error.value = ''
  busy.value = true
  result.value = null
  try {
    const data = new FormData()
    data.append('file', file.value)
    data.append('title', title.value)
    data.append('lon', lon.value)
    data.append('lat', lat.value)
    if (weight.value) data.append('weight', weight.value)
    if (category.value) data.append('category', category.value)
    const ps = await api('POST', '/v1/point-sets', { form: data })
    const out = { pointSet: ps, built: null }
    if (buildLayer.value) {
      const params =
        method.value === 'bin'
          ? { step: Number(step.value), agg: 'count' }
          : { bandwidth_m: Number(bandwidth.value) }
      out.built = await post('/v1/layers/build', {
        point_set_id: ps.id,
        method: method.value,
        slug: slug.value || slugify(title.value) || 'points',
        title: layerTitle.value || title.value,
        params,
      })
      emit('imported')
    }
    result.value = out
  } catch (e) {
    error.value = e.body?.message || e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section>
    <h2>Точки (CSV / GeoJSON / NDJSON)</h2>
    <form @submit.prevent="submit">
      <label>Файл <input type="file" accept=".csv,.geojson,.json,.ndjson" required @change="(e) => (file = e.target.files[0])" /></label>
      <label>Название набора <input v-model="title" required /></label>
      <label>Поле долготы <input v-model="lon" /></label>
      <label>Поле широты <input v-model="lat" /></label>
      <label>Поле веса (необязательно) <input v-model="weight" /></label>
      <label>Поле категории (необязательно) <input v-model="category" /></label>
      <label><input type="checkbox" v-model="buildLayer" /> и сразу построить слой</label>
      <template v-if="buildLayer">
        <label>
          Метод
          <select v-model="method">
            <option value="bin">биннинг (квадрат)</option>
            <option value="kde">тепловая карта (KDE)</option>
          </select>
        </label>
        <label v-if="method === 'bin'">Шаг, м <input v-model.number="step" type="number" min="1" /></label>
        <label v-else>Полоса, м <input v-model.number="bandwidth" type="number" min="1" /></label>
        <label>Слой: slug <input v-model="slug" pattern="[a-z0-9][a-z0-9_]*" /></label>
        <label>Слой: название <input v-model="layerTitle" /></label>
      </template>
      <button type="submit" :disabled="busy">Загрузить</button>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="result">
      Набор «{{ result.pointSet.title }}»: точек {{ formatValue(result.pointSet.count) }}, ошибок
      {{ result.pointSet.errors }}.
      <span v-if="result.built">Слой построен — опубликуйте его в каталоге выше.</span>
    </p>
  </section>
</template>
