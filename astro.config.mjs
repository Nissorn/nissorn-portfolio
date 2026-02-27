import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

import mdx from '@astrojs/mdx';
import starlight from '@astrojs/starlight';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';

export default defineConfig({
  vite: {
    plugins: [tailwindcss()]
  },

  markdown: {
    remarkPlugins: [remarkMath],
    rehypePlugins: [rehypeKatex],
  },

  integrations: [
    starlight({
      title: 'API Hub',
      // By default, Starlight will style its own pages and won't be affected by your global.css
      // because Starlight pages don't import your Layout.astro.
      /*
      sidebar: [
        {
          label: 'API Reference',
          items: openAPISidebarGroups,
        }
      ],
      plugins: [
        starlightOpenAPI([
          {
            base: 'docs/api',
            label: 'API',
            schema: './src/content/docs/api/schema.yaml',
          },
        ]),
      ],
      */
    }),
    mdx()
  ]
});