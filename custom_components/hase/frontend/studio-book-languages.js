// Book language is independent of the HA interface language.
export function resolveBookValue(value, requested, defaultLanguage, technical = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {value:value || technical,used:null,reason:value?"shared":"technical"};
  const keys=Object.keys(value).filter(key=>Array.isArray(value[key])?value[key].length:Boolean(value[key]));
  const keyFor=language=>{const normalized=String(language || "").toLowerCase().replaceAll("_","-");return keys.find(k=>k.toLowerCase().replaceAll("_","-")===normalized)||keys.find(k=>k.toLowerCase()===normalized.split("-")[0]);};
  for(const [language,reason] of [[requested,"requested"],["en","english"],[defaultLanguage,"default"]]) {
    const used=keyFor(language);if(used)return {value:value[used],used,reason};
  }
  if(keys.length===1)return {value:value[keys[0]],used:keys[0],reason:"only"};
  return {value:technical,used:null,reason:"technical"};
}
export function setBookValue(previous, language, value, defaultLanguage) {
  const result=previous && typeof previous==="object" && !Array.isArray(previous)?{...previous}:previous?{[defaultLanguage]:previous}:{};
  if(value && (!Array.isArray(value)||value.length))result[language]=value;
  else delete result[language];
  return result;
}
export function bookFallbacks(book, language, defaultLanguage) {
  const result=[];
  for(const obj of [book,...book.commands.flatMap(spell=>[spell,...(spell.expert?[{id:spell.id,...spell.expert}]:[])])]) {
    for(const field of ["name","description","trigger","triggers","examples","output","catchphrase"]) {
      if(!(field in obj))continue;
      const resolved=resolveBookValue(obj[field],language,defaultLanguage);
      if(!["requested","shared"].includes(resolved.reason))result.push({id:obj.id,field,...resolved});
    }
  }
  return result;
}
