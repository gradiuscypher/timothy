/**
 * The user IDs a page's actors name — what it adds to what it asks names for.
 *
 * Its own file rather than a corner of `ui.tsx`: that file exports components and
 * nothing else, which is what lets fast refresh keep their state across an edit.
 *
 * Takes a list and returns a list because that is how every caller uses it. `system` is
 * Timothy and has no ID, and a row's actor may not have loaded yet — both drop out here
 * rather than in the same conditional written five times at the call sites.
 */
export function actorIds(actors: Array<string | null | undefined>): string[] {
  return actors.flatMap((actor) =>
    !actor || actor === "system" ? [] : [actor.replace("user:", "")],
  );
}
