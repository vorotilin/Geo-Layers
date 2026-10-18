<script setup>
import { computed, ref } from 'vue'
import { api, formatValue, post, waitJob } from '../api.js'

const emit = defineEmits(['imported'])
const file = ref(null)
const form = ref({
  provider: '',
  title: '',
  period: '',
  key: 'zone_id',
  crs: '',
  column: 'val',
  slug: '',
  metricTitle: '',
  unit: '',
  value_kind: 'intensive',
  max_error_rate: 0.01,
})
const job = ref(null)
const report = ref(null)
const error = ref('')
const busy = ref(false)

// Манифест design §4. Форма — для одного столбца; несколько метрик — через API.
const manifest = computed(() => {
  const f = form.value
  const m = {
    kind: 'grid',
    dataset: { provider: f.provider, title: f.title },
    period: f.period,
    geometry: { type: 'geojson' },
    key: f.key,
    metrics: [
      {
        column: f.column,
        slug: f.slug,
        title: f.metricTitle || f.slug,
        unit: f.unit || null,
        value_kind: f.value_kind,
      },
    ],
    options: { max_error_rate: Number(f.max_error_rate) },
  }
  if (f.crs) m.crs = f.crs
  return m
})

// Подсказки из имени файла вида mean_income_home_2023Q1.geojson.
function onFile(e) {
  file.value = e.target.files[0] || null
  const name = (file.value?.name || '').replace(/\.(geo)?json$/i, '')
  const period = name.match(/(\d{4}Q[1-4])$/)
  if (period && !form.value.period) form.value.period = period[1]
  if (!form.value.slug) {
    form.value.slug = name
      .replace(/_?\d{4}Q[1-4]$/, '')
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, '_')
  }
}

function describe(e) {
  const errors = e.body?.details?.errors
  if (!errors) return e.message
  return `${e.message}: ` + errors.map((x) => `${x.loc.join('.')}: ${x.msg}`).join('; ')
}

async function submit() {
  error.value = ''
  report.value = null
  job.value = null
  busy.value = true
  try {
    const data = new FormData()
    data.append('file', file.value)
    const uploaded = await api('POST', '/v1/files', { form: data })
    const accepted = await post('/v1/imports', { file_id: uploaded.id, manifest: manifest.value })
    job.value = await waitJob(accepted.status_url, (j) => (job.value = j))
    report.value = (await api('GET', `/v1/imports/${accepted.import_id}`)).report
    if (job.value.status === 'succeeded') emit('imported')
  } catch (e) {
    error.value = describe(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section>
    <h2>Загрузить сетку (GeoJSON)</h2>
    <form @submit.prevent="submit">
      <label>Файл <input type="file" accept=".geojson,.json" required @change="onFile" /></label>
      <label>Поставщик <input v-model="form.provider" required /></label>
      <label>Набор данных <input v-model="form.title" required /></label>
      <label>Период <input v-model="form.period" required placeholder="2023Q1" /></label>
      <label>Ключ ячейки (поле) <input v-model="form.key" required /></label>
      <label>CRS, если в файле нет <input v-model="form.crs" placeholder="EPSG:32637" /></label>
      <label>Столбец значения <input v-model="form.column" required /></label>
      <label>Слой: slug <input v-model="form.slug" required pattern="[a-z0-9][a-z0-9_]*" /></label>
      <label>Слой: название <input v-model="form.metricTitle" /></label>
      <label>Единица <input v-model="form.unit" placeholder="руб." /></label>
      <label>
        Агрегация
        <select v-model="form.value_kind">
          <option value="intensive">среднее (доход, доля)</option>
          <option value="extensive">сумма (счётчик)</option>
        </select>
      </label>
      <label>
        Допустимая доля ошибок
        <input v-model="form.max_error_rate" type="number" min="0" max="1" step="0.001" />
      </label>
      <details>
        <summary class="muted">Манифест</summary>
        <pre>{{ JSON.stringify(manifest, null, 2) }}</pre>
      </details>
      <button type="submit" :disabled="busy">Импортировать</button>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="job">
      Задание: {{ job.status }}
      <span v-if="job.status === 'running'">{{ Math.round(job.progress) }} %</span>
      <span v-if="job.error" class="error"> — {{ job.error.message }}</span>
    </p>
    <div v-if="report">
      <p>
        Объектов {{ formatValue(report.features) }}, принято {{ formatValue(report.rows_ok) }},
        отброшено {{ formatValue(report.rows_failed) }}
        <span v-if="report.grid">
          · сетка {{ formatValue(report.grid.cells_total) }} ячеек (+{{
            formatValue(report.grid.cells_added)
          }})
        </span>
      </p>
      <p v-for="w in report.warnings" :key="w.warning" class="muted">{{ w.message }}</p>
      <table v-if="report.errors.length">
        <thead>
          <tr><th>Объект</th><th>Ошибка</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in report.errors.slice(0, 50)" :key="`${e.feature}-${e.error}`">
            <td>{{ e.feature }}</td>
            <td>
              {{ e.message }}<span v-if="e.column" class="muted"> ({{ e.column }})</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
