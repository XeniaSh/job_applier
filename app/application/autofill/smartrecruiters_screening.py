"""Generic OneClick Screening controls; employer answers live elsewhere.

    The selectors mirror the recorded open-shadow DOM. A widget whose selected
    state or option ownership cannot be proven stays manual.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.options import match_decline_to_answer_option, match_option_exact_normalized

_DEEP = """
const all = (root, sel) => {
  const out = [];
  const walk = r => { for (const e of r.querySelectorAll('*')) {
    if (e.matches(sel)) out.push(e); if (e.shadowRoot) walk(e.shadowRoot);
  }};
  if (root.shadowRoot) walk(root.shadowRoot); walk(root); return out;
};
const parent = e => e.parentElement || e.getRootNode().host || null;
const closest = (e, sel) => { for (let p=e; p; p=parent(p)) if(p.matches(sel)) return p; return null; };
const label = e => {
  let text = e.getAttribute('aria-label') || '';
  for (let p=e; p && !text; p=parent(p)) {
    const ids = (p.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
    if (ids.length) text = ids.map(id => {
      const hit = all(document, '[id]').filter(n => n.id === id);
      return hit.length === 1 ? hit[0].textContent : '';
    }).join(' ');
    if (!text && p.id) {
      const hit=all(document, 'label').filter(n => n.htmlFor === p.id);
      if(hit.length===1) text=hit[0].textContent;
    }
    if (!text) text=p.getAttribute('label') || '';
    if (p.matches('sr-screening-questions-form, oc-consent, oc-consent-decisions')) break;
  }
  return text.replace(/\\s+/g,' ').trim().replace(/\\s*\\*$/, '');
};
const required = e => {
  for(let p=e;p;p=parent(p)) {
    if(p.getAttribute('aria-required')==='true' || p.hasAttribute('required')) return true;
    if(p.matches('sr-screening-questions-form, oc-consent, oc-consent-decisions')) break;
  }
  return false;
};
"""

_SNAPSHOT = "() => {" + _DEEP + """
const roots=all(document,'sr-screening-questions-form[data-test="screening-questions-form"]');
if(roots.length!==1 || all(document,'oc-screening-questions').length!==1) return [];
const contexts=[[roots[0], 'questionnaire'],
  ...all(document,'oc-consent, oc-consent-decisions').map(r=>[r,'consent'])];
const seen=new Set(), out=[];
for(const [root,context] of contexts) for(const e of all(root,'input, textarea, select, [role="radiogroup"]')) {
  if(seen.has(e))continue; seen.add(e);
  if(!e.getClientRects().length || getComputedStyle(e).visibility==='hidden')continue;
  if(closest(e,'spl-dropdown-search') || e.type==='hidden')continue;
  const group=closest(e,'[role="radiogroup"]');
  if(group && group!==e)continue;
  let type=e.getAttribute('role')==='radiogroup'?'radio':
    e.getAttribute('role')==='combobox'?'combobox':
    e.tagName==='TEXTAREA'?'textarea':(e.type || 'text');
  if(type==='combobox' && closest(e,'spl-multiselect-autocomplete'))type='multiselect';
  const options=type==='radio'?all(e,'[role="radio"]').map(r=>label(r)||r.textContent.trim()):[];
  out.push({id:e.id || '',label:label(e),type,required:required(e),context,options});
}
return out;
}"""

_VALID = "e => {" + _DEEP + """
for(let p=e;p;p=parent(p)) {
  if(p.getAttribute('aria-invalid')==='true' || p.hasAttribute('errorstate'))return false;
  if(p.matches('sr-screening-questions-form, oc-consent, oc-consent-decisions'))break;
}
if(e.disabled || e.getAttribute('aria-disabled')==='true')return false;
if(e.checkValidity && !e.checkValidity())return false;
if(required(e) && e.type==='checkbox' && !e.checked)return false;
return true;
}"""

_HOST = "e => {" + _DEEP + "return closest(e, 'spl-autocomplete, spl-multiselect-autocomplete');}"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,180}")


def _norm(value: str) -> str:
    return " ".join(value.split())


class ScreeningControls:
    def __init__(self, stable, page_ok):
        self._stable = stable
        self._page_ok = page_ok
        self.proofs: dict[str, tuple[object, str, DiscoveredField, object]] = {}
        self.last_choice_selected = None

    def discover(self, page) -> list[DiscoveredField]:
        rows = page.evaluate(_SNAPSHOT)
        fields = []
        ids = [r['id'] for r in rows if r['id']]
        for index, row in enumerate(rows):
            identifiable = bool(row['label']) and (
                row['type'] == 'radio' or (row['id'] and ids.count(row['id']) == 1 and _SAFE_ID.fullmatch(row['id']))
            )
            key = row['id'] or f"radio:{row['label']}"
            fields.append(DiscoveredField(
                label=row['label'] or 'Unlabelled screening control', name=f"sr:{key}" if identifiable else f"sr:unknown-{index}",
                element_id=row['id'] or None, field_type=row['type'] if identifiable else 'unknown',
                required=row['required'], options=row['options'], context=row['context'],
            ))
        # Duplicate radiogroups/semantic identities are never answered.
        names = [f.name for f in fields]
        semantics = [(f.context, _norm(f.label).casefold().replace('’', "'"), f.field_type) for f in fields]
        for field, semantic in zip(fields, semantics):
            if names.count(field.name) != 1 or semantics.count(semantic) != 1:
                field.field_type = 'unknown'
        return fields

    def hydrated(self, page) -> bool:
        try:
            return bool(self.discover(page)) and page.locator('oc-nav-screening-questions').count() == 1
        except PlaywrightError:
            return False

    def _locator(self, page, field):
        if not self._page_ok(page):
            return None
        matches = [f for f in self.discover(page) if
                   (f.name, f.label, f.field_type, f.required, f.context) ==
                   (field.name, field.label, field.field_type, field.required, field.context)]
        if len(matches) != 1:
            return None
        root = page.locator('sr-screening-questions-form[data-test="screening-questions-form"]')
        if field.context == 'consent':
            root = page.locator('oc-consent, oc-consent-decisions')
        if field.field_type == 'radio':
            locator = root.get_by_role('radiogroup', name=field.label, exact=True)
        elif field.element_id and _SAFE_ID.fullmatch(field.element_id):
            locator = root.locator(f'input[id="{field.element_id}"], textarea[id="{field.element_id}"]')
        else:
            return None
        return locator if locator.count() == 1 else None

    def _options(self, page, locator):
        controlled = locator.get_attribute('aria-controls')
        if controlled and _SAFE_ID.fullmatch(controlled):
            boxes = page.locator('sr-screening-questions-form').locator(f'[role="listbox"][id="{controlled}"]')
            if boxes.count() == 1:
                return [o for o in boxes.locator('[role="option"]').element_handles() if o.is_visible()]
        host = locator.evaluate_handle(_HOST).as_element()
        return [o for o in host.query_selector_all('[role="listbox"] [role="option"]') if o.is_visible()] if host else []

    @staticmethod
    def _option_text(option):
        return _norm(option.get_attribute('aria-label') or option.inner_text())

    @staticmethod
    def _unique_match(wanted, texts):
        matches = [t for t in texts if match_option_exact_normalized(wanted, [t]) is not None]
        return matches[0] if len(matches) == 1 else None

    def fill(self, page, item) -> bool:
        self.last_choice_selected = None
        field, wanted = item.field, item.value
        self.proofs.pop(field.name, None)
        locator = self._locator(page, field)
        if locator is None:
            return False
        try:
            if field.field_type in {'text', 'textarea'} and isinstance(wanted, str):
                locator.fill(wanted)
                locator.evaluate('e => e.blur()')
                expected = wanted
            elif field.field_type == 'checkbox' and isinstance(wanted, bool):
                locator.set_checked(wanted)
                locator.evaluate('e => e.blur()')
                expected = wanted
            elif field.field_type == 'radio':
                options = locator.locator('[role="radio"]').element_handles()
                texts = [self._option_text(o) for o in options]
                target = 'Yes' if wanted is True else 'No' if wanted is False else str(wanted)
                matched = self._unique_match(target, texts)
                indexes = [i for i, t in enumerate(texts) if t == matched]
                if len(indexes) != 1:
                    return False
                options[indexes[0]].click()
                expected = texts[indexes[0]]
                self.last_choice_selected = expected
            elif field.field_type in {'combobox', 'multiselect'}:
                locator.click()
                last = []

                def options_ready():
                    nonlocal last
                    options = self._options(page, locator)
                    texts = [self._option_text(o) for o in options]
                    same = texts == last and bool(texts)
                    last = texts
                    return same

                if not self._stable(page, options_ready):
                    return False
                target = 'Yes' if wanted is True else 'No' if wanted is False else str(wanted)
                if target in {'decline_to_answer', 'prefer not to disclose'}:
                    declines = [t for t in last if match_decline_to_answer_option([t]) is not None]
                    matched = declines[0] if len(declines) == 1 else None
                else:
                    matched = self._unique_match(target, last)
                indexes = [i for i, t in enumerate(last) if t == matched]
                if len(indexes) != 1:
                    return False
                options = self._options(page, locator)
                if [self._option_text(o) for o in options] != last:
                    return False
                options[indexes[0]].click()
                locator.evaluate('e => e.blur()')
                expected = last[indexes[0]]
                self.last_choice_selected = expected
            else:
                return False
            self.proofs[field.name] = (page, page.url, field, expected)
            return self.read_back(page, field) is not None
        except PlaywrightError:
            return False

    def _value(self, locator, field):
        if not locator.evaluate(_VALID):
            return None
        if field.field_type == 'checkbox':
            return locator.is_checked()
        if field.field_type == 'radio':
            selected = [o for o in locator.locator('[role="radio"]').element_handles()
                        if o.evaluate("""e => {
                          const a=e.getAttribute('aria-checked');
                          return a==='true' ? typeof e.checked!=='boolean' || e.checked===true : a===null && e.checked===true;
                        }""")]
            return self._option_text(selected[0]) if len(selected) == 1 and selected[0].evaluate(_VALID) else None
        if field.field_type in {'combobox', 'multiselect'}:
            if locator.get_attribute('aria-expanded') == 'true':
                return None
            host = locator.evaluate_handle(_HOST).as_element()
            if host is None:
                return None
            selected = host.query_selector_all('[role="option"][aria-selected="true"]')
            if field.field_type == 'multiselect':
                return self._option_text(selected[0]) if len(selected) == 1 else None
            value = locator.input_value()
            if len(selected) == 1 and self._option_text(selected[0]) == value:
                return value
            # The inspected eligibility picker exposed this own Clear action
            # after selection. Text alone is not a committed choice.
            if not selected and len(host.query_selector_all('button[aria-label="Clear value"]')) == 1:
                return value
            return None
        value = locator.input_value()
        return value if value.strip() or not field.required else None

    def read_back(self, page, field) -> str | None:
        proof = self.proofs.get(field.name)
        if proof is None or proof[0] is not page or proof[1] != page.url:
            return None

        def committed():
            locator = self._locator(page, field)
            return locator is not None and self._value(locator, field) == proof[3]

        try:
            if not self._stable(page, committed):
                return None
            return ('true' if proof[3] else 'false') if isinstance(proof[3], bool) else str(proof[3])
        except PlaywrightError:
            return None
