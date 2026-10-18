<script setup>
import { onMounted, ref } from 'vue'
import { formatValue, get, post } from '../api.js'

const selection = defineModel({ default: null })
const layers = ref([])
const nextCursor = ref(null)
const detail = ref(null)
const error = ref('')

async function load(more = false) {
  error.value = ''
  try {
    const cursor = more && nextCursor.value ? `&cursor=${nextCursor.value}` : ''
    const page = await get(`/v1/layers?limit=50${cursor}`)
    layers.value = more ? [...layers.value, ...page.items] : page.items
    nextCursor.value = page.next_cursor
  } catch (e) {
    error.value = e.message
  }
}

async function open(layer) {
  detail.value = await get(`/v1/layers/${layer.id}`)
  const current = detail.value.versions.find((v) => v.id === detail.value.current_version_id)
  choose(current || detail.value.versions[0])
}

function choose(version) {
  selection.value = version ? { layer: detail.value, version } : null
}

async function publish(version) {
  error.value = ''
  try {
    await post(`/v1/layers/${detail.value.id}/versions/${version.id}/publish`)
    const id = detail.value.id
    detail.value = await get(`/v1/layers/${id}`)
    choose(detail.value.versions.find((v) => v.id === version.id))
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
defineExpose({ load })
</script>

<template>
  <section>
    <h2>Слои</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="!layers.length && !error" class="muted">Слоёв пока нет — загрузите файл ниже.</p>
    <ul class="plain">
      <li v-for="layer in layers" :key="layer.id">
        <button class="link" :class="{ selected: detail?.id === layer.id }" @click="open(layer)">
          {{ layer.title }}
        </button>
        <span class="muted"> · {{ layer.dataset.provider }} · {{ layer.versions }} верс.</span>
      </li>
    </ul>
    <button v-if="nextCursor" @click="load(true)">Ещё</button>
  </section>

  <section v-if="detail">
    <h2>{{ detail.title }} <span class="muted">{{ detail.unit || '' }} · {{ detail.value_kind }}</span></h2>
    <table>
      <thead><tr><th>Период</th><th>Рев.</th><th>Статус</th><th>Ячеек</th><th></th></tr></thead>
      <tbody>
        <tr v-for="v in detail.versions" :key="v.id">
          <td>
            <button class="link" :class="{ selected: selection?.version.id === v.id }" @click="choose(v)">
              {{ v.period }}
            </button>
          </td>
          <td>{{ v.revision }}</td>
          <td>{{ v.status }}<span v-if="v.id === detail.current_version_id"> (текущая)</span></td>
          <td>{{ formatValue(v.stats.count) }}</td>
          <td><button v-if="v.status === 'draft'" @click="publish(v)">Опубликовать</button></td>
        </tr>
      </tbody>
    </table>
    <p v-if="selection" class="muted">
      мин {{ formatValue(selection.version.stats.min) }} ·
      сред {{ formatValue(selection.version.stats.mean) }} ·
      макс {{ formatValue(selection.version.stats.max) }}
    </p>
  </section>
</template>
