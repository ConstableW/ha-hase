/* Presentation only. Book identity, capabilities and permissions come from HASE. */
export const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const paths = {
  plus:'<path d="M12 5v14M5 12h14"/>',
  book:'<path d="M3 4h6a4 4 0 0 1 3 2 4 4 0 0 1 3-2h6v15h-6a4 4 0 0 0-3 2 4 4 0 0 0-3-2H3zM12 6v15"/>',
  search:'<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/>',
  check:'<path d="m6 12 4 4 8-9"/><circle cx="12" cy="12" r="10"/>',
  refresh:'<path d="M20 7V2m0 5h-5M4 17v5m0-5h5M20 7a9 9 0 0 0-16 1m0 9a9 9 0 0 0 16-1"/>',
  import:'<path d="M4 15v6h16v-6M12 2v13m-5-5 5 5 5-5"/>',
  export:'<path d="M4 15v6h16v-6M12 16V2m-5 5 5-5 5 5"/>',
  settings:'<path d="m10 3-1 3-3 1-3 3 2 2-2 2 3 3 3 1 1 3h4l1-3 3-1 3-3-2-2 2-2-3-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
  clients:'<circle cx="12" cy="7" r="3"/><path d="M6 21v-4a6 6 0 0 1 12 0v4M3 6v5m18-5v5M9 21v-3m6 3v3"/>',
  spark:'<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8z"/>',
  grid:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
  list:'<path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1"/>',
  arrow:'<path d="M20 12H4m6-6-6 6 6 6"/>',
  shield:'<path d="M12 3 4 6v6c0 5 3.4 8.6 8 9.9 4.6-1.3 8-4.9 8-9.9V6z"/><circle cx="12" cy="10.4" r="2.2"/><path d="M8.3 16.1a4 4 0 0 1 7.4 0"/>',
};
export function icon(name) {return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.book}</svg>`;}
export function bookTheme(book) {
  const explicit=book.metadata?.visual?.theme;
  if(["sapphire","emerald","ember","amethyst"].includes(explicit))return explicit;
  if(/middle|earth|hdr|ring/i.test(book.id))return "emerald";
  if(/potter|fire|flame/i.test(book.id))return "ember";
  if(/command|conquer|cc/i.test(book.id))return "amethyst";
  return "sapphire";
}
export function safeCoverPath(value) {
  // Covers are optional same-origin assets. Never interpret imported markup or scripts.
  if(typeof value!=="string" || !value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f]/.test(value))return null;
  try {const url=new URL(value,"https://hase.invalid");return url.origin==="https://hase.invalid" ? url.pathname+url.search : null;} catch {return null;}
}
export function bookCover(book, {large=false}={}) {
  const theme=bookTheme(book), cover=typeof book.cover==="string" && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(book.cover)?book.cover:null;
  if(cover)return `<div class="book-art ${theme} ${large?"large":""}"><img src="${escapeHtml(cover)}" alt="${escapeHtml(typeof book.name==="string"?book.name:book.id)} – Cover" loading="lazy"></div>`;
  // Decorative book illustration, deliberately separate from the official HASE logo.
  const sigil = theme==="emerald"
    ? '<path d="M50 25v54M50 40 30 26m20 24 23-24M50 58 24 43m26 21 28-21M50 72 31 81m19-9 19 9M35 21l-5 5-8-1m45-5 6 6 8-1"/>'
    : theme==="ember" ? '<path d="m50 21 29 54H21zM50 21v54"/><circle cx="50" cy="57" r="17"/>'
    : theme==="amethyst" ? '<path d="m50 18 29 18v30L50 84 21 66V36zM50 31v40M32 41l36 20m0-20L32 61"/>'
    : '<path d="m50 18 8 25 24 7-24 8-8 24-8-24-24-8 24-7z"/><circle cx="50" cy="50" r="30"/>';
  return `<div class="book-art ${theme} ${large?"large":""}" aria-hidden="true"><div class="art-halo"></div><div class="art-particles">✦ <span>✧</span> ·</div><div class="grimoire"><div class="book-pages"></div><div class="book-face"><i class="corner tl"></i><i class="corner tr"></i><i class="corner bl"></i><i class="corner br"></i><svg viewBox="0 0 100 100" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"><circle cx="50" cy="50" r="42" stroke-width=".6" stroke-dasharray="2 4"/>${sigil}</svg><span class="book-engraving">SPELLBOOK</span></div><div class="book-spine"></div></div><div class="art-floor"></div></div>`;
}
export function filterBooks(books, query) {
  const normalized=String(query || "").trim().toLocaleLowerCase();
  return books.filter(book=>!normalized || JSON.stringify([book.name,book.id,book.description]).toLocaleLowerCase().includes(normalized));
}
export const actionIcons = {"new-book":"spark","new-spell":"plus","check-all":"check",check:"check",refresh:"refresh",import:"import","import-new":"import",export:"export","export-selected":"export",metadata:"book",settings:"settings",home:"arrow","close-editor":"arrow",expert:"spark","user-management":"shield"};
