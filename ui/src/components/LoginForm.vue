<script setup>
import { ref } from 'vue'
import { get } from '../api.js'

const emit = defineEmits(['login'])
const token = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await get('/v1/layers?limit=1', { token: token.value.trim() })
    emit('login', token.value.trim())
  } catch (e) {
    error.value = e.status === 401 ? 'Токен не подошёл' : e.message
  }
}
</script>

<template>
  <form class="panel" @submit.prevent="submit">
    <h1>Гео-слои</h1>
    <label>Токен API <input v-model="token" type="password" autocomplete="off" required /></label>
    <button type="submit">Войти</button>
    <p v-if="error" class="error">{{ error }}</p>
  </form>
</template>
