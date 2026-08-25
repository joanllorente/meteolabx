<script>
  import {
    Thermometer, Droplets, Droplet, Gauge, Wind, CloudRain, Box, CloudFog,
    AudioLines, Sun, SunMedium, Sprout, CloudSun, Sunrise, Scale, ArrowUp,
    ArrowDown, ArrowRight
  } from '@lucide/svelte';

  const map = {
    Thermometer, Droplets, Droplet, Gauge, Wind, CloudRain, Box, CloudFog,
    AudioLines, Sun, SunMedium, Sprout, CloudSun, Sunrise, Scale, ArrowUp,
    ArrowDown, ArrowRight
  };

  let { name, size = 18, ...rest } = $props();
  const Cmp = $derived(map[name] || Thermometer);
</script>

<Cmp {size} {...rest} />
