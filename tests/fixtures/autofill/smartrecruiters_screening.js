// Synthetic extension of smartrecruiters_page_one.html. Recorded hooks, shadow
// structure, questions and choices come from smartrecruiters-form-inspection.md.
// ALL transitions, retained/recreated DOM and failure switches are synthetic,
// not claims about the live framework's internal persistence implementation.
const S2 = window.S2_INIT || {};
window.__s2Actions = [];
const baseForm = document.querySelector('oc-oneclick-form');
const baseNav = document.querySelector('oc-nav-first-page');
baseNav.querySelector('[data-test="footer-submit"]').remove(); // stage-one fixture decoy
const next = baseNav.querySelector('[data-test="footer-next"]');
const screen = h('oc-screening-questions', {'data-test': 'screening-questions'});
const qhost = h('sr-screening-questions-form', {'data-test': 'screening-questions-form'});
screen.append(qhost); screen.hidden = true;
const questions = qhost.attachShadow({mode: 'open'});
questions.append(h('h2', {}, ['Preliminary questions']));
const consent = h('oc-consent'); consent.hidden = true;
const screenNav = h('oc-nav-screening-questions'); screenNav.hidden = true;
const back = h('button', {type: 'button', 'data-test': 'footer-back'}, ['Back']);
const submit = h('button', {type: 'button', 'data-test': 'footer-submit'}, ['Submit']);
screenNav.append(back, submit);
document.body.append(screen, consent, screenNav);
const baseURL = location.href;
const firstNameInput = () => baseForm.shadowRoot.querySelector('[data-test="personal-info-first-name-input"]').shadowRoot.querySelector('input');
const stageURL = () => { const u=new URL(baseURL); u.pathname += '/screening'; return u.href; };
const noticeLabel = 'What is your notice period?';
const eligibilityLabel = 'How are you eligible to work in this location?';
const officeLabel = 'We have a flexible working model at Wise. As part of our guidance, we expect Wisers to be in the office most days to build connections and learn from colleagues, particularly in their first six months. Do you live in a commutable distance from the office and are you willing to work from the office regularly?';
const auditLabel = 'Were you involved, in any capacity, in the audit of Wise’s financial statements, including concurring or second partner reviews or technical or industry consultation, in the last 12 months?';
let serial=0;
function textQuestion(label, required=true) {
  const el=input(label,'text',String(required), {id:'question_synthetic_'+serial++, maxlength:'200'});
  questions.append(shadowWrap('',el)); return el;
}
function combo(label, options, {required=true,multi=false}={}) {
  const id='question_synthetic_'+serial++;
  const host=h(multi?'spl-multiselect-autocomplete':'spl-autocomplete',{id,label,...(required?{required:''}:{})});
  const shadow=host.attachShadow({mode:'open'});
  const el=input(label,'text',String(required),{id,role:'combobox','aria-expanded':'false','aria-controls':id+'-list'});
  const list=h('div',{role:'listbox',id:id+'-list',...(multi?{'aria-multiselectable':'true'}:{})});
  list.hidden=true;
  list.append(...options.map(t=>h('div',{role:'option','aria-selected':'false'},[t])));
  el.addEventListener('click',()=>{list.hidden=false;el.setAttribute('aria-expanded','true');});
  list.addEventListener('mousedown',e=>e.preventDefault());
  list.addEventListener('click',e=>{
    if(e.target.getAttribute('role')!=='option')return;
    for(const o of list.children)o.setAttribute('aria-selected',String(o===e.target));
    el.value=multi?'':e.target.textContent;
    list.hidden=true;el.setAttribute('aria-expanded','false');
    if(S2.comboRevert && label===eligibilityLabel)setTimeout(()=>{el.value='';},25);
    if(S2.multiNoProof && multi)list.replaceChildren();
    if(S2.comboNoProof && !multi)for(const o of list.children)o.setAttribute('aria-selected','false');
  });
  shadow.append(el,list);questions.append(host);return el;
}
function radio(label) {
  const group=h('div',{role:'radiogroup','aria-label':label,'aria-required':'true'});
  for(const [name,value] of [['Yes','1'],['No','0']]) {
    const option=h('spl-radio',{role:'radio','aria-label':name,'aria-checked':'false',value,tabindex:'0'},[name]);
    option.addEventListener('click',()=>{
      for(const o of group.children)o.setAttribute('aria-checked',String(o===option));
      if(S2.radioRevert)setTimeout(()=>option.setAttribute('aria-checked','false'),25);
    });group.append(option);
  }
  questions.append(group);return group;
}
const notice=textQuestion(noticeLabel);
const eligibility=combo(eligibilityLabel,[
  'I hold citizenship or permanent residency (No visa/sponsorship required)',
  'I hold a temporary work visa / permit that allows me to work full-time',
  'I hold a student or temporary visa with work-hour restrictions',
  'I do not currently have work authorization and will require visa sponsorship','Other',
]);
if(S2.duplicateChoice) {
  const list=eligibility.getRootNode().querySelector('[role="listbox"]');
  list.append(h('div',{role:'option','aria-selected':'false'},[list.children[3].textContent.toUpperCase()]));
}
combo(officeLabel,["Yes - I'm willing to work from the office regularly",
  "Yes - I am willing to relocate and I'm willing to work from the office regularly",
  "No - I'm not able to work from the office regularly"]);
radio('Are you currently a Wise customer?');
radio('Does the advertised salary band meet your expectations?');
radio(auditLabel);
combo('Have you been employed by our external audit firm, PWC?',['Yes','No'],{required:false});
combo("What's your age?",['I prefer not to answer','26-34']);
combo('Are you part of the first generation of your family to study further education? e.g. college or university?',
  ['I prefer not to answer','Yes','No','I did not study further education']);
combo('What gender do you most closely identify with?',['I prefer not to answer','Man','Woman']);
combo('Do you identify with a gender that is different to the gender assigned to you at birth?',
  ['I prefer not to answer','Yes','No']);
combo('Are you Hispanic/Latin American? (https://wise.jobs/definitions)',['I prefer not to answer','Yes','No']);
// The report records these semantics rather than their full prompt text. These
// are sanitized synthetic transcriptions; exact live wording is unverified.
combo('Do you have a disability or neurodiversity, currently or previously?', ['I prefer not to answer','Yes','No']);
combo('US-applicant veteran status (https://wise.jobs/definitions)',
  ['I prefer not to answer', "Not applicable (if you're not applying from the US)", 'Yes, I am a veteran', 'No, I am not a veteran']);
const sexuality=combo('I identify my sexual orientation as (mark all that apply)',
  ['I prefer not to answer','Asexual','Bisexual'],{multi:true});
combo('What race/ethnicity do you most closely identify with (mark all that apply):',
  ['I prefer not to answer','Two or more races/Mixed-origin'],{multi:true});
textQuestion('If you have chosen to self describe, please specify below',false);
const langs=h('div',{'data-test':'question-language-row'},[
  input('Language','text','false',{id:'question_synthetic_lang-0-lang'}),
]);questions.append(langs);
function checkbox(label,required) {
  const el=input(label,'checkbox',String(required),{id:'consent_synthetic_'+serial++});
  consent.append(h('spl-checkbox',{'data-test':required?'consent-box':'synthetic-optional'},[el]));return el;
}
const privacy=checkbox('You declare that you have read and agree to the privacy notice of Wise.',true);
const marketing=checkbox('Receive Wise marketing updates',false);
const talent=checkbox('Join our talent pool',false);
if(S2.unknownRequired)textQuestion('Synthetic unknown required legal question');
if(S2.duplicateQuestion)questions.append(notice.cloneNode(true));
if(S2.duplicateLabel)textQuestion(noticeLabel);
if(S2.thinForm)for(const el of Array.from(questions.children))if(el!==notice.getRootNode().host)el.remove();
if(S2.searchDecoy)questions.append(h('spl-dropdown-search',{},[input('Search options','text','true')]));
let nextCount=0;
function show(screening) {
  baseForm.style.display=screening?'none':'';
  baseNav.hidden=screening;
  screen.hidden=!screening;consent.hidden=!screening;screenNav.hidden=!screening;
  history.pushState(null,'',screening?stageURL():baseURL);
}
next.addEventListener('click',()=>{
  window.__s2Actions.push('Next'); nextCount++;
  if(S2.validationFail) {
    baseForm.shadowRoot.querySelector('[data-test="personal-info-email-input"] input').setAttribute('aria-invalid','true');
    return;
  }
  if(S2.wrongPublication){history.pushState(null,'',stageURL().replace('11111111','99999999'));return;}
  show(true);
  if(S2.challenge)document.body.append(h('div',{class:'g-recaptcha'}));
  if(nextCount===2) {
    if(S2.loseAnswer)notice.value='';
    if(S2.invalidAnswer)notice.setAttribute('aria-invalid','true');
    if(S2.changeQuestion)notice.setAttribute('aria-label','Synthetic changed required question');
    if(S2.newRequired)textQuestion('Synthetic newly required question');
    if(S2.lateNewRequired)setTimeout(()=>textQuestion('Synthetic newly required question'),60);
    if(S2.dropResumeOnFinal)baseForm.shadowRoot.querySelector('.c-spl-file-list-item').remove();
    if(S2.loseCandidateOnFinal)firstNameInput().value='';
    if(S2.invalidCandidateOnFinal)baseForm.shadowRoot.querySelector('input#email-input').setAttribute('aria-invalid','true');
    if(S2.extraSelectedMulti)sexuality.getRootNode().querySelectorAll('[role="option"]')[1].setAttribute('aria-selected','true');
  }
});
back.addEventListener('click',()=>{
  window.__s2Actions.push('Back');show(false);
  if(S2.loseCandidate)firstNameInput().value='';
  if(S2.invalidCandidate)baseForm.shadowRoot.querySelector('input#email-input').setAttribute('aria-invalid','true');
  const row=baseForm.shadowRoot.querySelector('.c-spl-file-list-item');
  if(S2.dropResumeOnBack)row.remove();
  if(S2.replaceResumeOnBack)row.replaceWith(row.cloneNode(true));
});
submit.addEventListener('click',()=>window.__s2Actions.push('Submit'));
document.addEventListener('submit',()=>window.__s2Actions.push('form-submit'),true);
document.addEventListener('keydown',e=>{if(e.key==='Enter')window.__s2Actions.push('Enter');},true);
if(S2.navType)next.type=S2.navType;
if(S2.navFormAction)next.setAttribute('formaction','https://invalid.example/submit');
if(S2.navRename)next.textContent='Submit';
if(S2.navDuplicate)baseNav.append(next.cloneNode(true));
if(S2.navDisabled)next.disabled=true;
if(S2.backType)back.type=S2.backType;
window.s2State=()=>({actions:window.__s2Actions,notice:notice.value,privacy:privacy.checked,
  marketing:marketing.checked,talent:talent.checked,uploads:(window.__srUploads||[]).length,
  location:location.href,eligibility:eligibility.value});
