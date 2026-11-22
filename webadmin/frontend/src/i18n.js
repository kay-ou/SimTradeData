import { useSyncExternalStore } from 'react';
import translations from './translations.js';

const storageKey = 'simtradedata.language';
const supported = ['zh', 'en', 'de'];
const patterns = Object.entries(translations).filter(([key]) => key.includes('{0}')).sort(([a], [b]) => b.replace(/\{\d+\}/g, '').length - a.replace(/\{\d+\}/g, '').length);
const normalize = value => supported.includes(value?.split('-')[0]) ? value.split('-')[0] : 'en';
function initialLanguage() {
  try {
    const saved = localStorage.getItem(storageKey);
    if (supported.includes(saved)) return saved;
  } catch { /* Browser privacy settings may disable storage. */ }
  return normalize(navigator.languages?.[0] || navigator.language);
}
let language = initialLanguage();
const listeners = new Set();
export const locale = () => ({ zh: 'zh-CN', en: 'en-US', de: 'de-DE' })[language];
const subscribe = listener => { listeners.add(listener); return () => listeners.delete(listener); };
function applyLanguage(value) {
  language = value;
  document.documentElement.lang = locale();
  document.title = `SimTradeData · ${t('数据管理')}`;
  listeners.forEach(listener => listener());
}
function setLanguage(value) {
  if (!supported.includes(value)) return;
  try { localStorage.setItem(storageKey, value); } catch { /* Selection still works without storage. */ }
  applyLanguage(value);
}
export function useLanguage() {
  return [useSyncExternalStore(subscribe, () => language), setLanguage];
}
export function t(value, parameters = []) {
  if (typeof value !== 'string') return value;
  const key = value.trim();
  const index = language === 'de' ? 1 : 0;
  let result = language === 'zh' ? value : translations[value]?.[index] ?? (translations[key] ? value.replace(key, translations[key][index]) : value);
  if (language !== 'zh' && result === value && /[\u3400-\u9fff]/.test(value)) {
    for (const [pattern, variants] of patterns) {
      const expression = pattern.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{\d+\}/g, '(.+?)');
      const match = value.match(new RegExp(`^${expression}$`));
      if (match) { result = variants[index].replace(/\{(\d+)\}/g, (_, n) => t(match[Number(n) + 1])); break; }
    }
  }
  if (language !== 'zh' && result === value && value.includes('。') && value.split('。').filter(Boolean).length > 1) {
    result = value.match(/[^。]+。?/g).map(sentence => t(sentence)).join(' ');
  }
  return result.replace(/\{(\d+)\}/g, (_, n) => parameters[n] ?? `{${n}}`);
}
export function translateError(value) {
  const translated = t(value);
  if (language === 'zh' || !value || translated !== value) return translated;
  return `${language === 'de' ? 'Anfrage fehlgeschlagen' : 'Request failed'}: ${value}`;
}
window.addEventListener('storage', event => {
  if (event.key === storageKey && supported.includes(event.newValue)) applyLanguage(event.newValue);
});
applyLanguage(language);
