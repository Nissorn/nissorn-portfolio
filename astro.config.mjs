import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';


import starlight from '@astrojs/starlight';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';

import fs from 'node:fs';
import path from 'node:path';

// 1. Naming Mapping Configuration (e.g., 'ai-engine' -> 'AI Engine')
const projectNameMap = {
  'ai-engine': 'AI Engine',
  'thai-stylometry': 'Thai Stylometry',
  'study-guide': 'Study Guide AI'
};

const getProjectName = (folder) => projectNameMap[folder] || folder.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

const docsDir = path.resolve('./src/content/docs/docs');

// 2. Discover Projects and Schemas
let dynamicPlugins = [];
let dynamicSidebar = [];

if (fs.existsSync(docsDir)) {
  const folders = fs.readdirSync(docsDir, { withFileTypes: true })
    .filter(dirent => dirent.isDirectory())
    .map(dirent => dirent.name);

  const openApiConfigs = [];
  const folderSchemaMap = {};

  folders.forEach(folder => {
    const projectPath = path.join(docsDir, folder);
    const possibleSchemas = ['swagger.json', 'swagger.yaml', 'openapi.json', 'openapi.yaml'];

    // Scan for schema inside the project folder
    for (const file of possibleSchemas) {
      if (fs.existsSync(path.join(projectPath, file))) {
        folderSchemaMap[folder] = openApiConfigs.length;
        openApiConfigs.push({
          base: `docs/${folder}/api`,
          label: `${getProjectName(folder)} API`,
          schema: `./src/content/docs/docs/${folder}/${file}`,
        });
        break;
      }
    }
  });

  // 3. Construct the Starlight Plugins array ONLY if we found schemas
  if (openApiConfigs.length > 0) {
    dynamicPlugins.push(starlightOpenAPI(openApiConfigs));
  }

  // 4. Flatten the Sidebar Structure cleanly
  dynamicSidebar = folders.flatMap(folder => {
    const label = getProjectName(folder);

    const groups = [
      {
        label: `${label} Docs`,
        autogenerate: { directory: `docs/${folder}` },
        collapsed: true
      }
    ];

    if (folderSchemaMap[folder] !== undefined) {
      // Pass the reference directly to prevent Symbol corruption
      groups.push(openAPISidebarGroups[folderSchemaMap[folder]]);
    }

    // Check if this is the AI Engine project to add the Interactive Console Link
    if (folder === 'ai-engine') {
      groups.push({
        label: `${label} Interactive`,
        items: [
          {
            label: 'Try it out (Console)',
            link: '/docs/ai-engine/console',
            attrs: {
              target: '_blank'
            }
          }
        ]
      });
    }

    return groups;
  });
}

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
      title: 'Docs Hub',
      components: {
        Sidebar: './src/components/CustomSidebar.astro',
      },
      head: [], // Crucial: prevents the mergeHead crash
      plugins: dynamicPlugins,
      sidebar: dynamicSidebar
    })
  ]
});