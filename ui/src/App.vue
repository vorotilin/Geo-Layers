<script setup>
import { ref } from 'vue'
import { getToken, setToken } from './api.js'
import LoginForm from './components/LoginForm.vue'
import LayerPanel from './components/LayerPanel.vue'
import ImportForm from './components/ImportForm.vue'
import MapView from './components/MapView.vue'

const token = ref(getToken())
const selection = ref(null) // { layer, version }
const layersKey = ref(0) // смена ключа перечитывает каталог после импорта

function login(value) {
  setToken(value)
  token.value = value
}

function logout() {
  setToken('')
  token.value = ''
  selection.value = null
}
</script>

<template>
  <LoginForm v-if="!token" @login="login" />
  <div v-else class="layout">
    <aside class="panel">
      <h1>Гео-слои <button class="link muted" @click="logout">выйти</button></h1>
      <LayerPanel :key="layersKey" v-model="selection" />
      <ImportForm @imported="layersKey++" />
    </aside>
    <main class="map">
      <MapView :selection="selection" />
    </main>
  </div>
</template>
