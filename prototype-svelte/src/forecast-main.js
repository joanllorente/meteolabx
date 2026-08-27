import { mount } from 'svelte';
import ForecastApp from './ForecastApp.svelte';
import './app.css';

const target = document.getElementById('forecast-app');
target.replaceChildren();
const app = mount(ForecastApp, {
  target
});

export default app;
