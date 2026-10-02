import type { Avatar } from "./api";

export function mentionedAvatars(prompt: string, avatars: Avatar[]): Avatar[] {
  const handles = new Set(Array.from(prompt.matchAll(/(?<![\w@])@([a-zA-Z0-9_-]+)/g), (match) => match[1].toLowerCase()));
  return avatars.filter((avatar) => handles.has(avatar.handle));
}

export function generationReferences(prompt: string, references: string[], avatars: Avatar[]): string[] {
  return [...new Set([...references, ...mentionedAvatars(prompt, avatars).flatMap((avatar) => avatar.references)])];
}
