// Node module-loader hook: lets the Deno Edge Function source run unmodified under Node by
// pointing the `npm:@supabase/supabase-js@2` specifier at an in-memory fake.
const FAKE = new URL('./fake-supabase.ts', import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('npm:@supabase/supabase-js')) return { url: FAKE, shortCircuit: true };
  return nextResolve(specifier, context);
}
