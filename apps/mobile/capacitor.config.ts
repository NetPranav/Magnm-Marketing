import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.magnm.studio',
  appName: 'Magnm Studio',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
  },
};

export default config;
