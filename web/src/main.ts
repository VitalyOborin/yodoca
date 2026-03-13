import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './app/router';
import './app/styles/globals.css';
import 'highlight.js/styles/github-dark.css';

const app = createApp(App);

app.use(createPinia());
app.use(router);

app.mount('#app');
