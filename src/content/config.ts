// src/content/config.ts
import { defineCollection, z } from 'astro:content';

const notes = defineCollection({
    type: 'content',
    schema: z.object({
        title: z.string(),
        date: z.date(),
        description: z.string().optional(),
        tags: z.array(z.string()).default([]),
        image: z.string().optional(),
    }),
});

export const collections = { 'notes': notes };