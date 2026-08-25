import { mount } from 'svelte';
import ForecastApp from './ForecastApp.svelte';
import './app.css';

const app = mount(ForecastApp, {
  target: document.getElementById('forecast-app')
});

export default app;
