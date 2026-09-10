import { createApp } from 'vue'
import './assets/CSS/all.css'
import './assets/CSS/style.css'
import './assets/CSS/font.css'
import './assets/CSS/AfterTransition.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(router)
app.mount('#app')