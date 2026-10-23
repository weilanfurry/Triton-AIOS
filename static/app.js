
(function(){
  'use strict';
  var root = document.getElementById('triton-desktop');
  function $(id){ return document.getElementById(id); }
  function esc(s){ return String(s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function fmtSize(n){
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }
  function refreshIcons(){ if (window.lucide && lucide.createIcons){ lucide.createIcons({ attrs: { width: 16, height: 16 } }); } }
  function toast(msg){ var t = $('toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast._h); toast._h = setTimeout(function(){ t.classList.remove('show'); }, 2200); }
  if (typeof marked !== 'undefined' && marked.Renderer){
    try {
      var mdRenderer = new marked.Renderer();
      mdRenderer.link = function(href, title, text){
        try {
          var u = new URL(href, window.location.href);
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return text;
        } catch(e){ return text; }
        return '<a href="' + href.replace(/"/g, '&quot;') + '" target="_blank" rel="noopener">' + text + '</a>';
      };
      marked.use({ renderer: mdRenderer });
    } catch(e){}
  }
  function md(text){
    var safe = esc(text);
    if (typeof marked === 'undefined' || !marked.parse) return safe.replace(/\n/g, '<br>');
    try { return marked.parse(safe, { breaks: true, gfm: true }); }
    catch(e){ return safe.replace(/\n/g, '<br>'); }
  }
  function stepsHtml(steps){
    if (!steps || !steps.length) return '';
    var body = '';
    for (var i=0;i<steps.length;i++){
      body += esc(JSON.stringify(steps[i], null, 1)) + '\n';
    }
    return '<details class="steps-box">' +
      '<summary>调用记录 · ' + steps.length + ' 步</summary>' +
      '<pre class="steps">' + body + '</pre>' +
      '</details>';
  }

  /* 预览版自带的示例对话（服务端没有数据时展示，可随意删除） */
  var CONVS = [
    { id:'c1', icon:'sparkles', title:'Triton AIOS 能做什么？', time:'刚刚', msgs:[
      { role:'user', text:'Triton AIOS 能做什么？' },
      { role:'ai', text:'我是 Triton AIOS 的预览版：可以对话、调用技能、生成可运行的小应用，也能读写工作区文件。\n\n试试点输入框旁边的 Skill，或者让我「做个番茄钟小工具」。' }
    ]},
    { id:'c2', icon:'wand-sparkles', title:'AI 写应用：番茄钟', time:'刚刚', msgs:[
      { role:'user', text:'做个番茄钟小工具' },
      { role:'ai', text:'我会先读应用 SDK，然后生成 manifest.json + index.html，应用会直接出现在任务栏和启动台里。' }
    ]},
    { id:'c3', icon:'terminal', title:'写一个 Python 脚本', time:'刚刚', msgs:[
      { role:'user', text:'写一个读取 CSV 的 Python 脚本' },
      { role:'ai', text:'已生成示例脚本到 workspace/projects/，可以直接在 Python 运行时里打开运行。', steps:[{ step_id:1, tool:'file_write', args:{ path:'projects/hello.py' } }] }
    ]},
    { id:'c4', icon:'folder', title:'整理工作区文件', time:'刚刚', msgs:[
      { role:'user', text:'帮我列一下工作区里的文件' },
      { role:'agent', text:'工作区里现在有 fibonacci/、projects/ 和 README.md。' }
    ]},
    { id:'c5', icon:'globe', title:'联网搜索', time:'刚刚', msgs:[
      { role:'user', text:'帮我搜一下今天的科技新闻' },
      { role:'ai', text:'我会调用 web_search 搜索，再抓取正文，并在回答里附上来源链接。' }
    ]}
  ];
  var currentId = 'c1';
  var SKILLS = [];            // 技能面板数据（GET /api/skills）
  var activeSkill = null;     // 当前选中的技能，null = 普通对话
  var changes = 0;
  var files = [];

  function apiToFiles(list){
    var out = [];
    for (var i=0;i<list.length;i++){
      var f = list[i];
      if (f.type === 'dir'){
        out.push({ name:f.name, dir:true, path:f.path, children: apiToFiles(f.children || []) });
      } else {
        out.push({ name:f.name, path:f.path, size:f.size || 0, content:'' });
      }
    }
    return out;
  }

  function loadWorkspace(){
    return fetch('/api/workspace/list')
      .then(function(r){ return r.json(); })
      .then(function(d){
        files = apiToFiles((d && d.files) || []);
        renderTree();
        if ($('fm-list')) fmRender();
        return files;
      })
      .catch(function(){});
  }

  function getConv(){ for (var i=0;i<CONVS.length;i++){ if (CONVS[i].id===currentId) return CONVS[i]; } return CONVS[0]; }

  function scrollMsgs(){ var b=$('msgs'); if (b){ b.scrollTop = b.scrollHeight; } }

  function bindChips(box){
    var arr = box.querySelectorAll('[data-send]');
    for (var i=0;i<arr.length;i++){
      arr[i].addEventListener('click', function(){ sendMessage(this.getAttribute('data-send')); });
    }
    var openers = box.querySelectorAll('[data-open]');
    for (var j=0;j<openers.length;j++){
      openers[j].addEventListener('click', function(){
        var id = appIdByKind(this.getAttribute('data-open'));
        if (!id){ toast('未找到该应用，请确认 apps/ 下有对应文件夹'); return; }
        openApp(id);
      });
    }
  }

  function renderMsgs(){
    var conv = getConv();
    var box = $('msgs');
    if (!conv.msgs.length){ renderHero(box); return; }
    var html = '';
    for (var i=0;i<conv.msgs.length;i++){
      var m = conv.msgs[i];
      var cls = m.role==='user' ? 'msg user' : (m.role==='agent' ? 'msg ai agent' : 'msg ai');
      var avatar = m.role==='user' ? '<div class="avatar">我</div>' : '<div class="avatar"><i data-lucide="sparkles" aria-hidden="true"></i></div>';
      var chips = '';
      if (m.chips){
        chips = '<div class="chip-row">';
        for (var c=0;c<m.chips.length;c++){ chips += '<button type="button" class="chip" data-send="'+esc(m.chips[c])+'">'+esc(m.chips[c])+'</button>'; }
        chips += '</div>';
      }
      var steps = stepsHtml(m.steps);
      var atts = attsHtml(m.attachments);
      var skillTag = m.skill ? '<span class="msg-skill"><i data-lucide="wand-sparkles" aria-hidden="true"></i>' + esc(m.skill) + '</span>' : '';
      var bub = m.text ? '<div class="bubble md">' + md(m.text) + '</div>' : '';
      html += '<div class="'+cls+'">'+avatar+'<div class="bubble-wrap">'+skillTag+bub+atts+steps+chips+'</div></div>';
    }
    box.innerHTML = html;
    refreshIcons();
    bindChips(box);
  }

  function renderHero(box){
    box.innerHTML =
      '<div class="hero">' +
        '<div class="hero-logo"><i data-lucide="sparkles" aria-hidden="true"></i></div>' +
        '<h2>你好，我是 Triton</h2>' +
        '<p>一个 AI Native 桌面。可以对话，也可以调用技能生成应用、搜索网页、整理工作区。</p>' +
        '<div class="chips">' +
          '<button type="button" class="chip" data-send="Triton AIOS 能做什么？">✨ Triton AIOS 能做什么？</button>' +
          '<button type="button" class="chip" data-send="做个番茄钟小工具">✍️ 做个番茄钟小工具</button>' +
          '<button type="button" class="chip" data-send="写一个读取 CSV 的 Python 脚本">🐍 写一个 Python 脚本</button>' +
          '<button type="button" class="chip" data-open="python">▶️ 打开 Python 运行时</button>' +
          '<button type="button" class="chip" data-send="帮我列一下工作区里的文件">📁 工作区文件</button>' +
        '</div>' +
      '</div>';
    refreshIcons();
    bindChips(box);
  }

  /* ===== 对话列表排序：按最后一次对话时间倒序（最近聊过的在最上面） ===== */
  function pad2(n){ return (n < 10 ? '0' : '') + n; }

  function convById(id){
    for (var i=0;i<CONVS.length;i++){ if (CONVS[i].id === id) return CONVS[i]; }
    return null;
  }

  function convTimeLabel(c){
    // 老数据是「按原顺序估出来的时间戳」，只用来排序，显示仍用原来那个时间文字
    if (c && c.timeEstimated) return c.time || '';
    var ts = Number(c && c.updatedAt) || 0;
    if (!ts) return c && c.time ? c.time : '';       // 老数据没有时间戳时退回去用原字段
    var d = new Date(ts), now = new Date();
    var diff = now.getTime() - ts;
    if (diff < 60 * 1000) return '刚刚';
    if (diff < 60 * 60 * 1000) return Math.max(1, Math.floor(diff / 60000)) + ' 分钟前';
    if (d.toDateString() === now.toDateString()) return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    var y = new Date(now.getTime() - 86400000);
    if (d.toDateString() === y.toDateString()) return '昨天';
    if (d.getFullYear() === now.getFullYear()) return (d.getMonth() + 1) + '/' + d.getDate();
    return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate();
  }

  function sortConvs(){
    CONVS.sort(function(a, b){
      return (Number(b && b.updatedAt) || 0) - (Number(a && a.updatedAt) || 0);
    });
  }

  /* 有人在这个对话里说话：更新最后时间，并顶到列表最前面 */
  function touchConv(c){
    if (!c) return;
    c.updatedAt = Date.now();
    c.time = '刚刚';
    delete c.timeEstimated;           // 真实聊过了，时间标签改成根据真实时间计算
    sortConvs();
  }

  function renderConvs(){
    var box = $('sb-list');
    var html = '';
    for (var i=0;i<CONVS.length;i++){
      var c = CONVS[i];
      var cs = c.skill ? skillById(c.skill) : null;
      var timeLine = '<span class="sb-time"><span class="sb-when">' + esc(convTimeLabel(c)) + '</span>' +
        (cs ? '<span class="sb-skill-tag" title="技能：'+esc(cs.name)+'">' +
          '<i data-lucide="'+esc(cs.icon || 'wand-sparkles')+'" aria-hidden="true"></i>' + esc(cs.name) +
          '</span>' : '') +
        '</span>';
      html += '<div class="sb-item'+(c.id===currentId ? ' active':'')+'" data-id="'+c.id+'">' +
        '<span class="sb-ic"><i data-lucide="'+c.icon+'" aria-hidden="true"></i></span>' +
        '<span class="sb-meta"><span class="sb-name">'+esc(c.title)+'</span>'+timeLine+'</span>' +
        '<button type="button" class="sb-ren" data-ren="'+c.id+'" aria-label="重命名对话"><i data-lucide="pencil" aria-hidden="true"></i></button>' +
        '<button type="button" class="sb-del" data-del="'+c.id+'" aria-label="删除对话"><i data-lucide="trash-2" aria-hidden="true"></i></button>' +
      '</div>';
    }
    box.innerHTML = html;
    refreshIcons();
    var items = box.querySelectorAll('.sb-item');
    for (var j=0;j<items.length;j++){
      items[j].addEventListener('click', function(){ selectConv(this.getAttribute('data-id')); });
    }
    var dels = box.querySelectorAll('.sb-del');
    for (var k=0;k<dels.length;k++){
      dels[k].addEventListener('click', function(e){
        e.stopPropagation();
        var btn = this;
        if (btn.classList.contains('confirm')){
          deleteConversation(btn.getAttribute('data-del'));
          return;
        }
        btn.classList.add('confirm');
        btn.innerHTML = '<span>确认删除</span>';
        clearTimeout(btn._t);
        btn._t = setTimeout(function(){
          btn.classList.remove('confirm');
          btn.innerHTML = '<i data-lucide="trash-2" aria-hidden="true"></i>';
          refreshIcons();
        }, 2600);
        refreshIcons();
      });
    }
    var rens = box.querySelectorAll('.sb-ren');
    for (var r=0;r<rens.length;r++){
      rens[r].addEventListener('click', function(e){
        e.stopPropagation();
        startRename(this.getAttribute('data-ren'));
      });
    }
    var names = box.querySelectorAll('.sb-name');
    for (var n=0;n<names.length;n++){
      names[n].addEventListener('dblclick', function(e){
        e.stopPropagation();
        var item = this.closest('.sb-item');
        if (item) startRename(item.getAttribute('data-id'));
      });
    }
  }

  function startRename(id){
    var conv = null;
    for (var i=0;i<CONVS.length;i++){ if (CONVS[i].id===id) conv = CONVS[i]; }
    var item = $('sb-list').querySelector('.sb-item[data-id="'+id+'"]');
    if (!conv || !item) return;
    var nameEl = item.querySelector('.sb-name');
    if (!nameEl) return;
    var input = document.createElement('input');
    input.className = 'sb-rename-input';
    input.type = 'text';
    input.value = conv.title || '';
    input.setAttribute('aria-label', '对话名称');
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    var done = false;
    function finish(save){
      if (done) return;
      done = true;
      var v = (input.value || '').trim();
      if (save && v && v !== conv.title){
        conv.title = v;
        toast('已重命名对话');
      }
      renderConvs();
      updateChatHeader();
      saveConversations();
    }
    input.addEventListener('keydown', function(e){
      if (e.key === 'Enter'){ e.preventDefault(); finish(true); }
      else if (e.key === 'Escape'){ e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', function(){ finish(true); });
    input.addEventListener('click', function(e){ e.stopPropagation(); });
  }

  function deleteConversation(id){
    var idx = -1;
    for (var i=0;i<CONVS.length;i++){ if (CONVS[i].id===id){ idx = i; break; } }
    if (idx < 0) return;
    CONVS.splice(idx, 1);
    if (currentId === id){
      if (CONVS.length){
        currentId = CONVS[0].id;
      } else {
        newChat();
        return;
      }
    }
    syncSkillFromConv(true);
    renderConvs();
    renderMsgs();
    updateChatHeader();
    scrollMsgs();
    saveConversations();
    toast('对话已删除');
  }

  function selectConv(id){
    currentId = id;
    try { localStorage.setItem('triton.activeConv', currentId); } catch(e){}
    syncSkillFromConv(true);          // 每个对话有自己的技能
    renderConvs();
    renderMsgs();
    scrollMsgs();
    updateChatHeader();
    closeSidebar();
  }

  function updateChatHeader(){
    var c = getConv();
    $('chat-title').textContent = c.title === '新对话' ? 'Triton AIOS' : c.title;
  }

  function saveConversations(){
    clearTimeout(saveConversations._h);
    saveConversations._h = setTimeout(function(){
      try { localStorage.setItem('triton.activeConv', currentId); } catch(e){}
      fetch('/api/conversations', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ conversations: CONVS }) })
        .catch(function(){});
    }, 400);
  }

  function loadConversations(){
    fetch('/api/conversations')
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d && d.conversations && d.conversations.length){
          CONVS = d.conversations;
          // 老数据没有 updatedAt 时估一个：保持它们原来的先后顺序，
          // 并且排在任何「有真实时间」的对话下面（不能因为没时间戳就跳到最前面）
          var now = Date.now(), backfilled = false;
          var realTimes = [];
          for (var r=0;r<CONVS.length;r++){
            var rt = Number(CONVS[r].updatedAt) || 0;
            if (rt > 0) realTimes.push(rt);
          }
          var estimatedBase = realTimes.length ? Math.min.apply(null, realTimes) - 3600000 : now;
          var estimatedN = 0;
          for (var k=0;k<CONVS.length;k++){
            if (!CONVS[k].updatedAt){
              CONVS[k].updatedAt = estimatedBase - (estimatedN++) * 1000;
              CONVS[k].timeEstimated = true;
              backfilled = true;
            }
          }
          sortConvs();
          var ids = {};
          for (var i=0;i<CONVS.length;i++) ids[CONVS[i].id] = true;
          var savedActive = null;
          try { savedActive = localStorage.getItem('triton.activeConv'); } catch(e){}
          if (savedActive && ids[savedActive]) currentId = savedActive;
          else if (!ids[currentId]) currentId = CONVS[0].id;
          syncSkillFromConv(true);
          renderConvs();
          renderMsgs();
          updateChatHeader();
          scrollMsgs();
          if (backfilled) saveConversations();
        } else {
          saveConversations();
        }
      })
      .catch(function(){});
  }

  function newChat(){
    CONVS.unshift({ id:'c'+Date.now(), icon:'message-circle', title:'新对话', time:'刚刚', updatedAt:Date.now(), msgs:[], skill:null });
    currentId = CONVS[0].id;
    applySkill(null, true);           // 新对话从「普通对话」开始
    renderConvs();
    renderMsgs();
    updateChatHeader();
    closeSidebar();
    saveConversations();
  }

  function closeSidebar(){
    $('sidebar').classList.remove('open');
    $('sb-backdrop').classList.remove('show');
    $('btn-more').setAttribute('aria-expanded','false');
  }

  // ===== 技能（Skill）：入口按钮 + 弹出选择面板 + 生效条 =====
  function skillById(id){
    for (var i=0;i<SKILLS.length;i++){ if (SKILLS[i].id === id) return SKILLS[i]; }
    return null;
  }

  function loadSkills(){
    return fetch('/api/skills')
      .then(function(r){ return r.json(); })
      .then(function(d){
        SKILLS = (d && d.skills) || [];
        syncSkillFromConv(true);          // 技能可能被删掉/改名，按当前对话重新对齐
      })
      .catch(function(){});
  }

  function convSkillId(conv){
    return (conv && conv.skill) ? String(conv.skill) : '';
  }

  /* 把某个对话记着的技能同步到界面（切对话 / 载入对话 / 技能列表刷新时用） */
  function syncSkillFromConv(silent){
    var conv = getConv();
    var skill = conv ? skillById(convSkillId(conv)) : null;
    applySkill(skill, true);
    // 注意：技能列表还没加载完时（SKILLS 为空）不要误判成「技能被删了」
    if (conv && conv.skill && !skill && SKILLS.length){
      conv.skill = null;               // 这个技能已经被删了，静默清掉
      saveConversations();
    }
    if (silent) renderSkillPop();
  }

  /* 用户主动切换技能：只记在当前对话上，别的对话互不影响 */
  function setSkill(skill, silent){
    var conv = getConv();
    var id = skill ? skill.id : null;
    if (conv && convSkillId(conv) !== (id || '')){
      conv.skill = id;
      saveConversations();
    }
    applySkill(skill, silent);
    if (!silent) renderConvs();        // 侧栏里的技能标记跟着更新
  }

  function applySkill(skill, silent){
    activeSkill = skill || null;
    var on = !!activeSkill;
    var btn = $('skill-toggle');
    if (btn) btn.setAttribute('aria-pressed', on ? 'true':'false');
    $('pill-form').classList.toggle('skill-on', on);
    var badge = $('skill-badge');
    if (badge){
      badge.hidden = !on;
      if (on){
        badge.innerHTML = '<i data-lucide="'+esc(activeSkill.icon || 'sparkles')+'" aria-hidden="true"></i>' + esc(activeSkill.name);
      }
    }
    // 需要看工作目录的技能（例如 AI 写应用）自动展开右侧面板
    $('agent-panel').hidden = !(on && activeSkill.shows_workspace);
    if (on && activeSkill.shows_workspace) renderTree();
    var inp = $('chat-input');
    if (inp){
      inp.placeholder = (on && activeSkill.hint) ? activeSkill.hint : '输入消息，Enter 发送…（可 Ctrl+V 粘贴图片）';
    }
    renderSkillStrip();
    renderSkillPop();
    refreshIcons();
    if (on && !silent) toast('已启用技能：' + activeSkill.name);
  }

  function renderSkillStrip(){
    var strip = $('skill-strip');
    if (!strip) return;
    if (!activeSkill){ strip.hidden = true; strip.innerHTML = ''; return; }
    strip.hidden = false;
    strip.innerHTML =
      '<span class="skill-chip">' +
        '<i data-lucide="'+esc(activeSkill.icon || 'sparkles')+'" aria-hidden="true"></i>' +
        '<span>' + esc(activeSkill.name) + '</span>' +
        '<button type="button" class="sc-x" id="skill-clear" aria-label="退出技能"><i data-lucide="x" aria-hidden="true"></i></button>' +
      '</span>' +
      '<span class="skill-tip">' + esc(activeSkill.description || '') + '</span>';
    refreshIcons();
    var x = $('skill-clear');
    if (x) x.addEventListener('click', function(){ setSkill(null); closeSkillPop(); });
  }

  function renderSkillPop(){
    var box = $('sp-list');
    if (!box) return;
    var html = '<div class="sp-item'+(!activeSkill ? ' active':'')+'" data-skill="">' +
      '<span class="sp-ic"><i data-lucide="message-circle" aria-hidden="true"></i></span>' +
      '<span class="sp-meta"><span class="sp-name">普通对话</span>' +
      '<span class="sp-desc">不启用技能，直接用主控回答</span></span></div>';
    for (var i=0;i<SKILLS.length;i++){
      var s = SKILLS[i];
      var active = activeSkill && activeSkill.id === s.id;
      html += '<div class="sp-item'+(active ? ' active':'')+'" data-skill="'+esc(s.id)+'">' +
        '<span class="sp-ic"><i data-lucide="'+esc(s.icon || 'puzzle')+'" aria-hidden="true"></i></span>' +
        '<span class="sp-meta"><span class="sp-name">' + esc(s.name) +
          (s.source === 'custom' ? '<em class="sp-tag">自定义</em>' : '') +
        '</span><span class="sp-desc">' + esc(s.description || '') + '</span></span>' +
        (s.source === 'custom' ? '<span class="sp-del" data-del-skill="'+esc(s.id)+'" title="删除这个技能">×</span>' : '') +
        '</div>';
    }
    box.innerHTML = html;
    refreshIcons();
  }

  function openSkillPop(){
    var pop = $('skill-pop');
    if (!pop) return;
    pop.hidden = false;
    var btn = $('skill-toggle');
    if (btn) btn.setAttribute('aria-expanded', 'true');
    loadSkills();
  }

  function closeSkillPop(){
    var pop = $('skill-pop');
    if (pop) pop.hidden = true;
    var btn = $('skill-toggle');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  function toggleSkillPop(){
    var pop = $('skill-pop');
    if (!pop) return;
    if (pop.hidden) openSkillPop(); else closeSkillPop();
  }

  function uploadSkillFile(file){
    var name = file.name || 'skill.txt';
    if (!/\.(txt|json|md|text|prompt)$/i.test(name)){ toast('只支持 txt / json / md 提示词文件'); return; }
    if (file.size > 200 * 1024){ toast('文件太大了（上限 200KB）'); return; }
    var reader = new FileReader();
    reader.onload = function(){
      fetch('/api/skills/upload', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ filename:name, content:String(reader.result || '') })
      })
        .then(function(r){ return r.json().then(function(d){ return { ok:r.ok, data:d }; }); })
        .then(function(res){
          var d = res.data || {};
          if (!res.ok || !d.ok){ toast('添加技能失败：' + (d.detail || '未知错误')); return; }
          SKILLS = d.skills || SKILLS;
          renderSkillPop();
          setSkill(skillById(d.skill.id), true);
          closeSkillPop();
          toast('技能已添加：' + d.skill.name);
        })
        .catch(function(){ toast('添加技能失败：服务未响应'); });
    };
    reader.onerror = function(){ toast('读取文件失败'); };
    reader.readAsText(file);
  }

  function deleteSkill(id){
    var skill = skillById(id);
    if (!skill) return;
    fetch('/api/skills/' + encodeURIComponent(id), { method:'DELETE' })
      .then(function(r){ return r.json().then(function(d){ return { ok:r.ok, data:d }; }); })
      .then(function(res){
        if (!res.ok){ toast('删除失败：' + ((res.data && res.data.detail) || '未知错误')); return; }
        SKILLS = (res.data && res.data.skills) || [];
        if (activeSkill && activeSkill.id === id) setSkill(null, true);
        renderSkillPop();
        toast('已删除技能：' + skill.name);
      })
      .catch(function(){ toast('删除失败：服务未响应'); });
  }

  function bindSkillUi(){
    var btn = $('skill-toggle');
    if (btn) btn.addEventListener('click', function(e){ e.stopPropagation(); toggleSkillPop(); });
    var pop = $('skill-pop');
    if (pop){
      pop.addEventListener('click', function(e){
        var del = e.target.closest('[data-del-skill]');
        if (del){ e.stopPropagation(); deleteSkill(del.getAttribute('data-del-skill')); return; }
        var item = e.target.closest('[data-skill]');
        if (!item || !pop.contains(item)) return;
        e.stopPropagation();
        var id = item.getAttribute('data-skill');
        setSkill(id ? skillById(id) : null);
        closeSkillPop();
      });
    }
    var close = $('skill-pop-close');
    if (close) close.addEventListener('click', function(e){ e.stopPropagation(); closeSkillPop(); });
    var up = $('sp-upload');
    if (up) up.addEventListener('click', function(e){
      e.stopPropagation();
      var pick = $('pick-skill');
      if (pick) pick.click();
    });
    var pick = $('pick-skill');
    if (pick) pick.addEventListener('change', function(){
      var file = pick.files && pick.files[0];
      pick.value = '';
      if (file) uploadSkillFile(file);
    });
    document.addEventListener('click', function(e){
      var box = $('skill-pop');
      if (!box || box.hidden) return;
      if (box.contains(e.target)) return;
      if (e.target.closest && e.target.closest('#skill-toggle')) return;
      closeSkillPop();
    });
  }

  function startStream(conv, text, atts, skill){
    var el = document.createElement('div');
    el.className = 'msg ai streaming';
    el.innerHTML = '<div class="avatar"><i data-lucide="sparkles" aria-hidden="true"></i></div><div class="bubble-wrap"><div class="bubble md"></div><div class="step-box"></div></div>';
    $('msgs').appendChild(el);
    refreshIcons();
    scrollMsgs();
    var textEl = el.querySelector('.bubble');
    var stepBox = el.querySelector('.step-box');
    var raw = '';
    var steps = [];
    function renderText(){
      textEl.innerHTML = raw ? md(raw) : '<span class="thinking">正在思考…</span>';
      scrollMsgs();
    }
    renderText();
    fetch('/api/chat/stream', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ message:text, session_id:currentId, attachments: atts || [], skill: skill ? skill.id : null }) })
      .then(function(resp){
        if (!resp.ok || !resp.body){
          return resp.json().then(function(d){ throw new Error((d.error && d.error.message) || ('HTTP ' + resp.status)); });
        }
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = '';
        function pump(){
          return reader.read().then(function(res){
            if (res.done) return;
            buf += decoder.decode(res.value, { stream:true });
            var sep;
            while ((sep = buf.indexOf('\n\n')) >= 0){
              var part = buf.slice(0, sep);
              buf = buf.slice(sep + 2);
              if (part.indexOf('data:') !== 0) continue;
              var payload = part.slice(5).trim();
              if (!payload) continue;
              var ev;
              try { ev = JSON.parse(payload); } catch(e){ continue; }
              if (ev.type === 'delta'){ raw += ev.text || ''; renderText(); }
              else if (ev.type === 'tool'){
                steps.push({ step_id: ev.step_id, tool: ev.tool, args: ev.args || {} });
                stepBox.innerHTML = stepsHtml(steps);
                scrollMsgs();
              }
              else if (ev.type === 'play'){ playSongByName(ev.name); }
              else if (ev.type === 'open_window'){ handleOpenWindow(ev); }
              else if (ev.type === 'ui_command'){ applyUiCommands(ev.commands || []); }
              else if (ev.type === 'app_changed'){ onAppChanged(ev); }
              else if (ev.type === 'ui_reload'){ onUiReload(); }
              else if (ev.type === 'skill_changed'){ loadSkills(); }
              else if (ev.type === 'skill_auto'){ onSkillAuto(ev); }
              else if (ev.type === 'error'){ raw = (raw ? raw + '\n' : '') + '出错了：' + ev.message; renderText(); }
            }
            return pump();
          });
        }
        return pump();
      })
      .catch(function(e){
        raw = (raw ? raw + '\n' : '') + '连接主控失败：' + e.message;
        renderText();
      })
      .then(function(){
        el.remove();
        conv.msgs.push({ role:'ai', text: raw, steps: steps, skill: skill ? skill.name : '' });
        touchConv(conv);
        renderConvs();
        renderMsgs();
        scrollMsgs();
        saveConversations();
      });
  }

  // 技能产生的真实副作用：刷新应用列表 / 重载界面 / 提示自动启用
  function onAppChanged(ev){
    var appId = ev && ev.app_id ? String(ev.app_id) : '';
    loadApps().then(function(){ if (appId) refreshAppWindow(appId); });
    if (appId) toast('应用已更新：' + appId + '（可在任务栏 / 启动台打开）');
  }

  function refreshAppWindow(appId){
    var frames = document.querySelectorAll('.win .app-frame');
    for (var i=0;i<frames.length;i++){
      var win = frames[i].closest('.win');
      if (!win) continue;
      if (win.getAttribute('data-ui-key') !== 'win:' + appId) continue;
      var base = frames[i].getAttribute('data-src') || frames[i].getAttribute('src') || '';
      if (!base) continue;
      frames[i].setAttribute('src', base.split('?')[0] + '?_=' + Date.now());
    }
  }

  function onUiReload(){
    toast('界面已更新，正在刷新桌面…');
    clearTimeout(onUiReload._h);
    onUiReload._h = setTimeout(function(){ window.location.reload(); }, 800);
  }

  function onSkillAuto(ev){
    if (ev && ev.skill && ev.skill.name) toast('已自动启用技能：' + ev.skill.name);
  }

  // ===== 输入框附件：粘贴图片 / 按钮选择文件与图片 =====
  var attachments = [];
  var MAX_ATTACH_BYTES = 25 * 1024 * 1024;
  var sendingAttachments = false;

  function attIcon(name){
    var ext = (String(name || '').indexOf('.') >= 0) ? String(name).split('.').pop().toLowerCase() : '';
    if (ext === 'py') return 'file-code';
    if (ext === 'md' || ext === 'txt' || ext === 'log') return 'file-text';
    if (ext === 'json' || ext === 'yml' || ext === 'yaml') return 'braces';
    if (ext === 'zip' || ext === 'rar' || ext === '7z' || ext === 'tar' || ext === 'gz') return 'file-archive';
    if (ext === 'mp3' || ext === 'wav' || ext === 'm4a' || ext === 'flac' || ext === 'ogg') return 'music';
    if (ext === 'mp4' || ext === 'mov' || ext === 'webm' || ext === 'mkv') return 'video';
    if (ext === 'pdf') return 'file-type';
    if (ext === 'csv' || ext === 'xlsx' || ext === 'xls') return 'table';
    return 'file';
  }

  function isImageFile(f){
    if (!f) return false;
    return /^image\//i.test(f.type || '') || /\.(png|jpe?g|gif|webp|bmp|svg|avif|heic|ico)$/i.test(f.name || '');
  }

  function readAsDataUrl(file){
    return new Promise(function(resolve, reject){
      var fr = new FileReader();
      fr.onload = function(){ resolve(String(fr.result || '')); };
      fr.onerror = function(){ reject(fr.error || new Error('读取失败')); };
      fr.readAsDataURL(file);
    });
  }

  function addAttachments(fileList){
    var list = Array.prototype.slice.call(fileList || []);
    if (!list.length) return;
    var skipped = 0;
    var jobs = [];
    for (var i=0;i<list.length;i++){
      (function(f){
        if (!f || !f.size){ skipped++; return; }
        if (f.size > MAX_ATTACH_BYTES){ skipped++; return; }
        var image = isImageFile(f);
        var item = {
          id: 'att-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7),
          name: f.name || (image ? '粘贴的图片.png' : '附件'),
          kind: image ? 'image' : 'file',
          size: f.size,
          mime: f.type || '',
          file: f,
          preview: ''
        };
        attachments.push(item);
        if (image){
          jobs.push(readAsDataUrl(f).then(function(url){ item.preview = url; }).catch(function(){}));
        }
      })(list[i]);
    }
    renderAttachments();
    if (skipped) toast('有 ' + skipped + ' 个文件超过 25MB 或为空，已跳过');
    if (jobs.length){
      Promise.all(jobs).then(function(){ renderAttachments(); });
    }
    if (list.length - skipped > 0) toast('已添加 ' + (list.length - skipped) + ' 个附件');
  }

  function removeAttachment(id){
    for (var i=0;i<attachments.length;i++){
      if (attachments[i].id === id){ attachments.splice(i, 1); break; }
    }
    renderAttachments();
  }

  function renderAttachments(){
    var strip = $('attach-strip');
    if (!strip) return;
    if (!attachments.length){
      strip.hidden = true;
      strip.innerHTML = '';
      return;
    }
    strip.hidden = false;
    var html = '';
    for (var i=0;i<attachments.length;i++){
      var a = attachments[i];
      var thumb = a.preview
        ? '<img class="attach-thumb" src="'+a.preview+'" alt="">'
        : '<span class="attach-ic"><i data-lucide="'+(a.kind === 'image' ? 'image' : attIcon(a.name))+'" aria-hidden="true"></i></span>';
      html += '<div class="attach-chip">' + thumb +
        '<span class="attach-meta">' +
          '<span class="attach-name" title="'+esc(a.name)+'">'+esc(a.name)+'</span>' +
          '<span class="attach-size">'+(a.kind === 'image' ? '图片 · ' : '')+fmtSize(a.size)+'</span>' +
        '</span>' +
        '<button type="button" class="attach-x" data-del-att="'+a.id+'" aria-label="移除附件">' +
          '<i data-lucide="x" aria-hidden="true"></i></button>' +
      '</div>';
    }
    strip.innerHTML = html;
    refreshIcons();
    var btns = strip.querySelectorAll('[data-del-att]');
    for (var j=0;j<btns.length;j++){
      btns[j].addEventListener('click', function(){ removeAttachment(this.getAttribute('data-del-att')); });
    }
  }

  function attsHtml(list){
    if (!list || !list.length) return '';
    var html = '';
    for (var i=0;i<list.length;i++){
      var a = list[i];
      var src = a.url || a.preview || '';
      var inner = (a.kind === 'image' && src)
        ? '<img src="'+esc(src)+'" alt="">'
        : '<i data-lucide="'+attIcon(a.name)+'" aria-hidden="true"></i>';
      html += '<span class="bubble-att" title="'+esc(a.path || a.name)+'">'+inner +
        '<span class="ba-nm">'+esc(a.name)+'</span></span>';
    }
    return '<div class="bubble-atts">' + html + '</div>';
  }

  function fileToBase64(file){
    return readAsDataUrl(file).then(function(url){
      var comma = url.indexOf(',');
      return comma >= 0 ? url.slice(comma + 1) : url;
    });
  }

  function uploadAttachments(list){
    var uploaded = [];
    var chain = Promise.resolve();
    for (var i=0;i<list.length;i++){
      (function(a){
        chain = chain.then(function(){
          return fileToBase64(a.file).then(function(b64){
            return fetch('/api/workspace/upload', {
              method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({ name:a.name, data:b64 })
            });
          }).then(function(r){
            return r.json().then(function(d){
              if (!r.ok || !d.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
              uploaded.push({ name:d.name, path:d.path, size:d.size, mime:d.mime, kind:d.kind, url:d.url });
            });
          }).catch(function(err){
            toast('「' + a.name + '」上传失败：' + (err && err.message ? err.message : err));
          });
        });
      })(list[i]);
    }
    return chain.then(function(){ return uploaded; });
  }

  function setSending(on){
    sendingAttachments = !!on;
    var btn = $('send-btn');
    if (!btn) return;
    btn.disabled = !!on;
    btn.innerHTML = on ? '<i data-lucide="loader" aria-hidden="true"></i>' : '<i data-lucide="arrow-up" aria-hidden="true"></i>';
    refreshIcons();
  }

  function hasFileDrag(e){
    var dt = e.dataTransfer;
    if (!dt) return false;
    if (dt.types){
      for (var i=0;i<dt.types.length;i++){ if (dt.types[i] === 'Files') return true; }
    }
    return !!(dt.files && dt.files.length);
  }

  function bindComposerAttachments(){
    var input = $('chat-input');
    var pickFile = $('pick-file');
    var pickImage = $('pick-image');
    var btnFile = $('btn-pick-file');
    var btnImage = $('btn-pick-image');

    if (btnFile && pickFile){
      btnFile.addEventListener('click', function(){ pickFile.click(); });
      pickFile.addEventListener('change', function(){
        addAttachments(pickFile.files);
        pickFile.value = '';
      });
    }
    if (btnImage && pickImage){
      btnImage.addEventListener('click', function(){ pickImage.click(); });
      pickImage.addEventListener('change', function(){
        addAttachments(pickImage.files);
        pickImage.value = '';
      });
    }

    // 复制粘贴：截图 / 复制的图片文件直接变成附件，纯文本仍然走默认粘贴
    if (input){
      input.addEventListener('paste', function(e){
        var dt = e.clipboardData;
        if (!dt) return;
        var files = [];
        var items = dt.items || [];
        for (var i=0;i<items.length;i++){
          if (items[i].kind === 'file'){
            var f = items[i].getAsFile();
            if (f) files.push(f);
          }
        }
        if (!files.length && dt.files && dt.files.length){
          files = Array.prototype.slice.call(dt.files);
        }
        if (!files.length) return;
        e.preventDefault();
        addAttachments(files);
      });
    }

    // 拖拽文件到对话窗口
    var chat = document.querySelector('#triton-desktop .chat');
    var pill = $('pill-form');
    if (chat && pill){
      chat.addEventListener('dragover', function(e){
        if (!hasFileDrag(e)) return;
        e.preventDefault();
        pill.classList.add('drop');
      });
      chat.addEventListener('dragleave', function(e){
        if (!chat.contains(e.relatedTarget)) pill.classList.remove('drop');
      });
      chat.addEventListener('drop', function(e){
        if (!hasFileDrag(e)) return;
        e.preventDefault();
        pill.classList.remove('drop');
        addAttachments(e.dataTransfer.files);
      });
    }
  }

  function sendMessage(raw, atts){
    var text = String(raw || '').trim();
    var attached = atts || [];
    if (!text && !attached.length) return;
    maybeOpenWindowFromText(text);   // 用户要窗口就立刻开，不等模型决定
    var conv = getConv();
    conv.msgs.push({ role:'user', text:text, attachments: attached.length ? attached : undefined });
    touchConv(conv);                  // 聊过的对话顶到列表最前面
    if (!conv.title || conv.title === '新对话'){
      var base = text || (attached[0] && attached[0].name) || '附件';
      conv.title = base.length > 18 ? base.slice(0, 18) + '…' : base;
      updateChatHeader();
    }
    renderConvs();
    renderMsgs();
    scrollMsgs();
    // 技能跟着对话走：用这个对话自己记着的技能，而不是全局状态
    startStream(conv, text, attached, skillById(convSkillId(conv)));
  }

  function treeHtml(f, depth){
    var pad = 'padding-left:' + (8 + depth*14) + 'px;';
    if (f.dir){
      var kids = '';
      for (var i=0;i<f.children.length;i++){ kids += treeHtml(f.children[i], depth+1); }
      return '<div class="tree-item folder" style="'+pad+'"><i data-lucide="folder" aria-hidden="true"></i><span class="nm">'+esc(f.name)+'/</span></div>' + kids;
    }
    return '<div class="tree-item file" style="'+pad+'"><i data-lucide="file-text" aria-hidden="true"></i><span class="nm">'+esc(f.name)+'</span><span class="sz">'+fmtSize(f.size || (f.content||'').length)+'</span></div>';
  }

  function renderTree(){
    var box = $('ap-tree');
    var html = '';
    for (var i=0;i<files.length;i++){ html += treeHtml(files[i], 0); }
    box.innerHTML = html;
    refreshIcons();
  }

  function updateCount(){
    $('ap-count').textContent = changes + ' 次改动';
  }

  function syncFileViews(){
    renderTree();
    updateCount();
    if ($('fm-list')) fmRender();
  }

  // ===== 文件应用：文件管理器 =====
  var fmPath = '';
  var fmSelected = null;
  var fmOpenPath = null;
  var fmMenuPath = null;

  function wsApi(url, method, body){
    return fetch(url, { method:method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) })
      .then(function(r){ return r.json().then(function(d){ return { status:r.status, data:d }; }); });
  }

  function fmFormatTime(ms){
    if (!ms) return '';
    var d = new Date(ms * 1000);
    function p(n){ return (n < 10 ? '0' : '') + n; }
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function fmIconName(entry){
    if (entry.dir) return 'folder';
    var ext = entry.name.indexOf('.') >= 0 ? entry.name.split('.').pop().toLowerCase() : '';
    if (ext === 'py') return 'file-code';
    if (ext === 'md' || ext === 'txt') return 'file-text';
    if (ext === 'json') return 'braces';
    if (ext === 'mp3' || ext === 'm4a' || ext === 'flac' || ext === 'wav') return 'music';
    if (ext === 'png' || ext === 'jpg' || ext === 'jpeg' || ext === 'gif') return 'image';
    return 'file';
  }

  function fmEntry(path){
    if (!path) return null;
    var parts = path.split('/');
    var list = files;
    for (var i=0;i<parts.length;i++){
      var found = null;
      for (var j=0;j<list.length;j++){
        if (list[j].name === parts[i]){ found = list[j]; break; }
      }
      if (!found) return null;
      if (i === parts.length - 1) return found;
      list = found.children || [];
    }
    return null;
  }

  function fmEntries(path){
    if (!path) return files;
    var dir = fmEntry(path);
    return (dir && dir.dir) ? (dir.children || []) : [];
  }

  function fmRender(){
    var list = $('fm-list');
    if (!list) return;
    var pathEl = $('fm-path');
    if (pathEl) pathEl.textContent = 'workspace' + (fmPath ? ' / ' + fmPath.split('/').join(' / ') : '');
    var entries = fmEntries(fmPath);
    if (!entries.length){
      list.innerHTML = '<div class="fm-empty">这个文件夹是空的</div>';
      return;
    }
    var html = '';
    for (var i=0;i<entries.length;i++){
      var e = entries[i];
      var p = e.path || e.name;
      html += '<div class="fm-row'+(fmSelected === p ? ' sel':'')+'" data-path="'+esc(p)+'" title="'+esc(e.name)+'">' +
        '<span class="fm-ic"><i data-lucide="'+fmIconName(e)+'" aria-hidden="true"></i></span>' +
        '<span class="fm-name">'+esc(e.name)+'</span>' +
        '<span class="fm-size">'+(e.dir ? '文件夹' : fmtSize(e.size || 0))+'</span>' +
        '<span class="fm-time">'+fmFormatTime(e.mtime)+'</span>' +
      '</div>';
    }
    list.innerHTML = html;
    refreshIcons();
    var rows = list.querySelectorAll('.fm-row');
    for (var r=0;r<rows.length;r++){
      rows[r].addEventListener('click', function(){ fmSelect(this.getAttribute('data-path')); });
      rows[r].addEventListener('dblclick', function(){ fmOpen(this.getAttribute('data-path')); });
      rows[r].addEventListener('contextmenu', function(ev){
        ev.preventDefault();
        ev.stopPropagation();
        fmSelect(this.getAttribute('data-path'));
        fmMenu(this.getAttribute('data-path'), ev);
      });
    }
  }

  function fmSelect(path){ fmSelected = path; fmRender(); }

  function fmOpen(path){
    var entry = fmEntry(path);
    if (!entry) return;
    if (entry.dir){
      fmPath = path;
      fmSelected = null;
      fmHideEditor();
      fmRender();
      return;
    }
    fmOpenFile(path);
  }

  function fmHideEditor(){
    fmOpenPath = null;
    var ed = $('fm-editor');
    var list = $('fm-list');
    var head = $('fm-head');
    if (ed) ed.hidden = true;
    if (list) list.hidden = false;
    if (head) head.hidden = false;
  }

  function fmOpenFile(path){
    var entry = fmEntry(path);
    if (!entry || entry.dir) return;
    fmOpenPath = path;
    var ed = $('fm-editor');
    var list = $('fm-list');
    var head = $('fm-head');
    var nameEl = $('fm-editor-name');
    var textEl = $('fm-text');
    var out = $('fm-out');
    if (ed) ed.hidden = false;
    if (list) list.hidden = true;
    if (head) head.hidden = true;
    if (nameEl) nameEl.textContent = path;
    if (out){ out.hidden = true; out.textContent = ''; }
    if (textEl) textEl.value = '读取中…';
    fetch('/api/workspace/read?path=' + encodeURIComponent(path))
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d.content === undefined){
          if (textEl) textEl.value = '读取失败：' + (d.detail || '');
          return;
        }
        if (textEl) textEl.value = d.content;
        if (nameEl) nameEl.textContent = path + '（' + fmtSize(d.size || 0) + '）';
      })
      .catch(function(){ if (textEl) textEl.value = '读取失败：服务未响应'; });
    if (textEl) textEl.focus();
  }

  function fmMenu(path, ev){
    var menu = $('fm-menu');
    var box = $('fm');
    if (!menu || !box) return;
    fmMenuPath = path;
    menu.hidden = false;
    var rect = box.getBoundingClientRect();
    var mw = menu.offsetWidth || 116;
    var mh = menu.offsetHeight || 100;
    var x = ev.clientX - rect.left;
    var y = ev.clientY - rect.top;
    menu.style.left = Math.max(4, Math.min(x, rect.width - mw - 4)) + 'px';
    menu.style.top = Math.max(4, Math.min(y, rect.height - mh - 4)) + 'px';
  }

  function fmHideMenu(){
    var menu = $('fm-menu');
    if (menu) menu.hidden = true;
    fmMenuPath = null;
  }

  function fmAction(act){
    var path = fmMenuPath;
    fmHideMenu();
    if (!path) return;
    if (act === 'open') fmOpen(path);
    else if (act === 'rename') fmInlineRename(path);
    else if (act === 'delete') fmDelete(path);
  }

  function fmInlineRename(path){
    var list = $('fm-list');
    if (!list) return;
    var rows = list.querySelectorAll('.fm-row');
    var row = null;
    for (var i=0;i<rows.length;i++){ if (rows[i].getAttribute('data-path') === path){ row = rows[i]; break; } }
    if (!row) return;
    var nameEl = row.querySelector('.fm-name');
    if (!nameEl) return;
    var parts = path.split('/');
    var oldName = parts[parts.length - 1];
    var input = document.createElement('input');
    input.className = 'fm-rename';
    input.type = 'text';
    input.value = oldName;
    input.setAttribute('aria-label', '新名称');
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    var done = false;
    function finish(save){
      if (done) return;
      done = true;
      var v = (input.value || '').trim();
      if (!save || !v || v === oldName){ fmRender(); return; }
      wsApi('/api/workspace/rename', 'POST', { path:path, new_name:v }).then(function(r){
        if (r.status >= 400){
          toast('重命名失败：' + ((r.data && r.data.detail) || r.status));
          fmRender();
          return;
        }
        if (fmOpenPath === path) fmOpenPath = r.data.new_path || v;
        toast('已重命名为 ' + v);
        loadWorkspace().then(function(){ fmRender(); });
      });
    }
    input.addEventListener('keydown', function(e){
      if (e.key === 'Enter'){ e.preventDefault(); finish(true); }
      else if (e.key === 'Escape'){ e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', function(){ finish(true); });
    input.addEventListener('click', function(e){ e.stopPropagation(); });
  }

  function fmDelete(path){
    if (!window.confirm('确定删除 ' + path + ' 吗？')) return;
    wsApi('/api/workspace/delete', 'POST', { path:path }).then(function(r){
      if (r.status >= 400){
        toast('删除失败：' + ((r.data && r.data.detail) || r.status));
        return;
      }
      if (fmOpenPath === path) fmHideEditor();
      if (fmSelected === path) fmSelected = null;
      toast('已删除 ' + path);
      loadWorkspace().then(function(){ fmRender(); });
    });
  }

  function fmNew(isDir){
    var nameEl = $('fm-name');
    var name = (nameEl && nameEl.value || '').trim();
    if (!name){ toast('先在右侧输入框填名称'); return; }
    var path = (fmPath ? fmPath + '/' : '') + name;
    var req = isDir
      ? wsApi('/api/workspace/mkdir', 'POST', { path:path })
      : wsApi('/api/workspace/write', 'PUT', { path:path, content:'' });
    req.then(function(r){
      if (r.status >= 400){
        toast((isDir ? '新建文件夹失败：' : '新建文件失败：') + ((r.data && r.data.detail) || r.status));
        return;
      }
      if (nameEl) nameEl.value = '';
      toast((isDir ? '已新建文件夹 ' : '已新建文件 ') + name);
      loadWorkspace().then(function(){
        fmRender();
        if (!isDir) fmOpenFile(path);
      });
    });
  }

  function fmSave(){
    if (!fmOpenPath){ toast('没有打开的文件'); return; }
    wsApi('/api/workspace/write', 'PUT', { path:fmOpenPath, content:$('fm-text').value }).then(function(r){
      if (r.status >= 400){ toast('保存失败：' + ((r.data && r.data.detail) || r.status)); return; }
      toast('已保存 workspace/' + fmOpenPath);
      loadWorkspace().then(function(){ fmRender(); });
    });
  }

  function fmRun(){
    if (!fmOpenPath){ toast('请先打开一个 .py 文件'); return; }
    var out = $('fm-out');
    if (out){ out.hidden = false; out.textContent = '正在运行 ' + fmOpenPath + ' …'; }
    fetch('/api/workspace/run', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ path:fmOpenPath }) })
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (!out) return;
        if (d.error){ out.textContent = '运行失败：' + d.error; return; }
        out.textContent = '退出码 ' + d.exit_code + '（' + d.duration + 's）\n' + (d.stdout || '') + (d.stderr ? '\n[stderr]\n' + d.stderr : '');
      })
      .catch(function(){ if (out) out.textContent = '运行失败：服务未响应'; });
  }

  function fmHandoff(){
    if (!fmOpenPath){ toast('请先打开一个文件'); return; }
    var inp = $('chat-input');
    if (!inp) return;
    inp.value = '请修改 workspace/' + fmOpenPath + '：';
    inp.focus();
    toast('已填入对话输入框，补充需求后发送');
  }

  function bindFileApp(){
    function bind(id, fn){ var el = $(id); if (el) el.addEventListener('click', fn); }
    bind('fm-back', function(){
      if (!fmPath){ toast('已经在工作区根目录'); return; }
      var parts = fmPath.split('/');
      parts.pop();
      fmPath = parts.join('/');
      fmSelected = null;
      fmHideEditor();
      fmRender();
    });
    bind('fm-refresh', function(){ loadWorkspace().then(function(){ fmRender(); toast('已刷新'); }); });
    bind('fm-new-file', function(){ fmNew(false); });
    bind('fm-new-dir', function(){ fmNew(true); });
    bind('fm-save', fmSave);
    bind('fm-run', fmRun);
    bind('fm-agent', fmHandoff);
    bind('fm-editor-back', function(){ fmHideEditor(); fmRender(); });
    var menu = $('fm-menu');
    if (menu){
      var acts = menu.querySelectorAll('[data-act]');
      for (var i=0;i<acts.length;i++){
        acts[i].addEventListener('click', function(){ fmAction(this.getAttribute('data-act')); });
      }
      menu.addEventListener('contextmenu', function(e){ e.preventDefault(); });
    }
    var list = $('fm-list');
    if (list){
      list.addEventListener('contextmenu', function(e){
        if (e.target.closest('.fm-row')) return;
        e.preventDefault();
        fmHideMenu();
      });
      list.addEventListener('click', function(e){
        if (!e.target.closest('.fm-row')){ fmSelected = null; fmRender(); fmHideMenu(); }
      });
    }
    document.addEventListener('click', function(){ fmHideMenu(); });
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape') fmHideMenu(); });
    fmHideEditor();
    fmRender();
  }

  var songs = [];
  var playingIdx = -1;
  var audio = null;

  /* 播放器元素常驻桌面根节点，绑定只做一次（窗口反复开关不会重复挂监听） */
  function bindAudio(){
    audio = $('mp-audio');
    if (!audio || audio.getAttribute('data-bound') === '1') return audio;
    audio.setAttribute('data-bound', '1');
    audio.addEventListener('timeupdate', updateProgress);
    audio.addEventListener('loadedmetadata', updateProgress);
    audio.addEventListener('play', function(){
      var b = $('mp-toggle'); if (b) b.textContent = '暂停';
      setDockPlaying(true);
    });
    audio.addEventListener('pause', function(){
      var b = $('mp-toggle'); if (b) b.textContent = '播放';
      setDockPlaying(false);
    });
    audio.addEventListener('ended', function(){ playIndex(playingIdx + 1); });
    return audio;
  }

  /* 任务栏音乐图标上的「后台播放中」指示灯 */
  function setDockPlaying(on){
    var id = musicAppId();
    var items = document.querySelectorAll('#triton-desktop .dock-item[data-app]');
    for (var i=0;i<items.length;i++){
      if (items[i].getAttribute('data-app') === id) items[i].classList.toggle('playing', !!on);
    }
  }

  function fmtTime(t){
    if (!isFinite(t)) return '--:--';
    var m = Math.floor(t / 60), s = Math.floor(t % 60);
    return m + ':' + ('0' + s).slice(-2);
  }

  function updateProgress(){
    var bar = $('mp-bar'), dur = $('mp-dur');
    if (!bar || !audio) return;
    if (audio.duration && isFinite(audio.duration)){
      bar.style.width = Math.min(100, audio.currentTime / audio.duration * 100) + '%';
    }
    if (dur) dur.textContent = fmtTime(audio.currentTime) + ' / ' + fmtTime(audio.duration);
  }

  function renderMusicList(){
    var box = $('music-list');
    if (!box) return;
    if (!songs.length){
      box.innerHTML = '<div class="music-empty">目录里没有可播放的音频文件</div>';
      return;
    }
    var html = '';
    for (var i=0;i<songs.length;i++){
      var s = songs[i];
      html += '<button type="button" class="m-item'+(i===playingIdx ? ' playing':'')+'" data-idx="'+i+'">' +
        '<span class="m-ic"><i data-lucide="music" aria-hidden="true"></i></span>' +
        '<span class="m-name">'+esc(s.title)+'</span><span class="m-art">本地音乐 · '+s.ext+'</span><span class="m-dur"></span>' +
      '</button>';
    }
    box.innerHTML = html;
    refreshIcons();
    var btns = box.querySelectorAll('.m-item');
    for (var j=0;j<btns.length;j++){
      btns[j].addEventListener('click', function(){ toggleSong(Number(this.getAttribute('data-idx'))); });
    }
  }

  function loadMusic(){
    var box = $('music-list');
    if (box) box.innerHTML = '<div class="music-empty">正在读取音乐目录…</div>';
    return fetch('/api/music/list')
      .then(function(r){ return r.json(); })
      .then(function(d){
        songs = (d && d.songs) || [];
        if (d && d.error){
          if (box) box.innerHTML = '<div class="music-empty">'+esc(d.error)+'</div>';
          return songs;
        }
        renderMusicList();
        return songs;
      })
      .catch(function(){
        if (box) box.innerHTML = '<div class="music-empty">音乐目录读取失败</div>';
        return songs;
      });
  }

  /* 窗口打开时把界面状态同步成当前播放状态（后台播放的歌在窗口里也能看到） */
  function refreshMusicUi(){
    var title = $('mp-title'), artist = $('mp-artist');
    if (!title || !audio) return;
    if (playingIdx >= 0 && songs[playingIdx]){
      title.textContent = songs[playingIdx].title;
      if (artist) artist.textContent = '本地音乐';
    } else {
      title.textContent = '未播放';
    }
    var toggle = $('mp-toggle');
    if (toggle) toggle.textContent = audio.paused ? '播放' : '暂停';
    renderMusicList();
    updateProgress();
  }

  function playIndex(i){
    if (i < 0 || i >= songs.length) return;
    bindAudio();
    if (!audio){ toast('播放器不可用'); return; }
    var s = songs[i];
    playingIdx = i;
    var title = $('mp-title'), artist = $('mp-artist');
    if (title) title.textContent = s.title;
    if (artist) artist.textContent = '本地音乐';
    audio.src = s.url;
    audio.play().catch(function(){});
    setDockPlaying(true);
    fetch('/api/music/recent', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name:s.name }) }).catch(function(){});
    renderMusicList();
    updateProgress();
  }

  function toggleSong(i){
    var s = songs[i];
    if (!s || !audio) return;
    if (playingIdx === i && !audio.paused){
      audio.pause();
      renderMusicList();
      return;
    }
    if (playingIdx === i){
      audio.play().catch(function(){});
      renderMusicList();
      return;
    }
    playIndex(i);
  }

  /* AI 触发的播放：只在后台放声音，不打开播放器窗口。
     想看到播放器就点任务栏上的音乐图标（图标会亮起后台播放指示灯）。 */
  function playSongByName(name){
    var q = String(name || '').toLowerCase().replace(/\.mp3$/, '');
    function tryPlay(){
      var hit = -1;
      for (var i=0;i<songs.length;i++){
        if (songs[i].name.toLowerCase() === q || songs[i].title.toLowerCase() === q){ hit = i; break; }
      }
      if (hit < 0){
        for (var j=0;j<songs.length;j++){
          if (songs[j].title.toLowerCase().indexOf(q) >= 0 || q.indexOf(songs[j].title.toLowerCase()) >= 0){ hit = j; break; }
        }
      }
      if (hit < 0){
        toast('音乐库里没有找到：' + name);
        return;
      }
      playIndex(hit);
      toast('后台播放中：' + songs[hit].title + '（未打开窗口）');
    }
    if (songs.length){ tryPlay(); return; }
    loadMusic().then(tryPlay).catch(function(){ toast('音乐列表读取失败'); });
  }

  /* ===== UI 修改：前端上报界面快照 + 执行小 AI 下发的指令（可还原） =====
     小 AI 走 ui_list / ui_apply 两个工具；这里负责真实改样式，并把改完的状态回报给后端。 */
  var UI_OVERRIDES = {};        // key -> { props: {属性: 原值}, text: 原文字 }
  var UI_LAST_APPLIED = [];     // 最近一次执行结果，随下一次快照回报
  var uiSnapshotTimer = null;
  var uiControlSel = '';
  var UI_CLIENT = 'page-' + Math.random().toString(36).slice(2, 8);   // 区分多个标签页的界面快照

  function uiKeyOf(el){
    if (!el) return '';
    return el.getAttribute('data-ui-key') || el.id || '';
  }

  function uiResolve(key){
    if (!key) return null;
    var el = document.getElementById(key);
    if (el) return el;
    return root.querySelector('[data-ui-key="' + String(key).replace(/"/g, '\\"') + '"]');
  }

  function collectUiSnapshot(){
    var rootRect = root.getBoundingClientRect();
    var out = [{
      key: 'triton-desktop', type: 'div', text: '桌面根容器',
      rect: [0, 0, root.clientWidth, root.clientHeight], visible: true, app: null, window: null
    }];
    var nodes = root.querySelectorAll('[id], [data-ui-key]');
    for (var i=0;i<nodes.length && out.length<150;i++){
      var el = nodes[i];
      if (!el || el.id === 'triton-desktop' || el.closest('#triton-tip') || el.closest('#toast')) continue;
      var key = uiKeyOf(el);
      if (!key) continue;
      var r = el.getBoundingClientRect();
      var win = el.closest('.win');
      var label = String(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '')
        .replace(/\s+/g, ' ').trim().slice(0, 40);
      var onScreen = r.width > 0 && r.height > 0 &&
        (r.left + r.width) > rootRect.left + 2 && r.left < rootRect.right - 2 &&
        (r.top + r.height) > rootRect.top + 2 && r.top < rootRect.bottom - 2;
      out.push({
        key: key,
        type: el.tagName.toLowerCase(),
        text: label,
        rect: [Math.round(r.left - rootRect.left), Math.round(r.top - rootRect.top), Math.round(r.width), Math.round(r.height)],
        visible: !!onScreen,
        app: el.getAttribute('data-app') || null,
        window: win ? win.getAttribute('data-ui-key') : null
      });
    }
    return out;
  }

  function postUiSnapshot(){
    clearTimeout(uiSnapshotTimer);
    uiSnapshotTimer = setTimeout(function(){
      var payload = {
        client: UI_CLIENT,
        viewport: { w: root.clientWidth, h: root.clientHeight },
        windows: wins.map(function(w){ return { id: w.id, key: 'win:' + w.id, minimized: !!w.minimized }; }),
        elements: collectUiSnapshot(),
        last_applied: UI_LAST_APPLIED
      };
      UI_LAST_APPLIED = [];
      fetch('/api/ui/snapshot', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) })
        .catch(function(){});
    }, 350);
  }

  function uiOverride(key){
    if (!UI_OVERRIDES[key]) UI_OVERRIDES[key] = { props: {}, applied: {}, text: undefined, textApplied: undefined };
    return UI_OVERRIDES[key];
  }

  function uiSetProp(key, el, prop, value){
    var o = uiOverride(key);
    if (!(prop in o.props)) o.props[prop] = el.style.getPropertyValue(prop) || '';   // 原值（用于还原）
    o.applied[prop] = value;                                                        // 改后的值（用于重贴）
    el.style.setProperty(prop, value);
  }

  function uiSetElText(el, value){
    // 尽量只替换文字，保留按钮里的图标
    var nodes = el.childNodes;
    for (var i=nodes.length-1;i>=0;i--){
      var n = nodes[i];
      if (n.nodeType === 3 && String(n.nodeValue || '').trim()){
        n.nodeValue = value;
        return;
      }
    }
    if (!el.children.length){ el.textContent = value; return; }
    var span = el.querySelector(':scope > span');
    if (span){ span.textContent = value; return; }
    var s = document.createElement('span');
    s.textContent = value;
    el.appendChild(s);
  }

  function uiRestoreElement(key){
    var el = uiResolve(key);
    var o = UI_OVERRIDES[key];
    if (!o) return false;
    if (el){
      for (var p in o.props){
        if (o.props[p]) el.style.setProperty(p, o.props[p]);
        else el.style.removeProperty(p);
      }
      if (o.text !== undefined) uiSetElText(el, o.text);
    }
    delete UI_OVERRIDES[key];
    return true;
  }

  function applyUiCommand(cmd){
    var key = String((cmd && cmd.target) || '');
    var action = String((cmd && cmd.action) || '').toLowerCase();
    if (action === 'reset' && key === '*'){
      var keys = Object.keys(UI_OVERRIDES);
      for (var i=0;i<keys.length;i++) uiRestoreElement(keys[i]);
      return { target: '*', action: 'reset', ok: true, message: '已还原全部界面修改（' + keys.length + ' 处）' };
    }
    var el = uiResolve(key);
    if (!el) return { target: key, action: action, ok: false, message: '没找到这个元素（窗口可能没打开）' };
    if (action === 'reset'){
      var had = !!UI_OVERRIDES[key];
      uiRestoreElement(key);
      return { target: key, action: 'reset', ok: true, message: had ? ('已还原 ' + key) : (key + ' 本来就没改过') };
    }
    var o = uiOverride(key);
    var message = '';
    if (action === 'move'){
      var rootRect = root.getBoundingClientRect();
      var r = el.getBoundingClientRect();
      var x = Math.round(Number(cmd.x));
      var y = Math.round(Number(cmd.y));
      var pos = window.getComputedStyle(el).position || '';
      if (pos === 'absolute' || pos === 'fixed'){
        uiSetProp(key, el, 'left', x + 'px');
        uiSetProp(key, el, 'top', y + 'px');
        message = '已移动到 (' + x + ', ' + y + ')';
      } else {
        var dx = x - Math.round(r.left - rootRect.left);
        var dy = y - Math.round(r.top - rootRect.top);
        uiSetProp(key, el, 'transform', 'translate(' + dx + 'px,' + dy + 'px)');
        message = '已位移 (' + dx + ', ' + dy + ')';
      }
    } else if (action === 'resize'){
      if (cmd.width) uiSetProp(key, el, 'width', Math.round(Number(cmd.width)) + 'px');
      if (cmd.height) uiSetProp(key, el, 'height', Math.round(Number(cmd.height)) + 'px');
      message = '已缩放到 ' + (cmd.width ? Math.round(Number(cmd.width)) : '原宽') + '×' + (cmd.height ? Math.round(Number(cmd.height)) : '原高');
    } else if (action === 'hide'){
      uiSetProp(key, el, 'display', 'none');
      message = '已隐藏';
    } else if (action === 'show'){
      uiOverride(key);
      o.applied.display = null;          // null = 这次不设值（相当于去掉 display）
      el.style.removeProperty('display');
      message = '已显示';
    } else if (action === 'color'){
      var prop = cmd.property || 'background';
      uiSetProp(key, el, prop, cmd.value);
      message = '已改 ' + prop + ' = ' + cmd.value;
    } else if (action === 'text'){
      if (o.text === undefined) o.text = String(el.innerText || '').replace(/\s+/g, ' ').trim();
      o.textApplied = String(cmd.value == null ? '' : cmd.value);
      uiSetElText(el, o.textApplied);
      message = '已改文字';
    } else if (action === 'style'){
      var props = cmd.properties || {};
      for (var name in props) uiSetProp(key, el, name, props[name]);
      message = '已改样式：' + Object.keys(props).join('、');
    }
    return { target: key, action: action, ok: true, message: message };
  }

  function applyUiCommands(cmds){
    var results = [];
    for (var i=0;i<(cmds || []).length;i++) results.push(applyUiCommand(cmds[i]));
    UI_LAST_APPLIED = results;
    var okCount = 0;
    for (var j=0;j<results.length;j++){ if (results[j].ok) okCount++; }
    toast('已按小 AI 的指令修改界面（' + okCount + '/' + results.length + ' 处生效）');
    postUiSnapshot();
    renderControl();
    return results;
  }

  /* 窗口 / 图标被重新创建后，把之前改过的样式再贴回去 */
  function reapplyUiOverrides(){
    var changed = false;
    for (var key in UI_OVERRIDES){
      var el = uiResolve(key);
      if (!el) continue;
      var o = UI_OVERRIDES[key];
      for (var p in o.applied){
        var v = o.applied[p];
        if (v === null || v === '') el.style.removeProperty(p);
        else el.style.setProperty(p, v);
      }
      if (o.textApplied !== undefined) uiSetElText(el, o.textApplied);
      changed = true;
    }
    if (changed) postUiSnapshot();
  }

  function renderControl(){
    var box = $('sc-elems');
    if (!box) return;
    var items = collectUiSnapshot();
    var html = '';
    for (var i=0;i<items.length;i++){
      var it = items[i];
      html += '<button type="button" class="sc-elem' + (uiControlSel === it.key ? ' sel' : '') + '" data-key="' + esc(it.key) + '">' +
        '<span class="sc-name">' + esc(it.key) + '</span>' +
        '<span class="sc-tag">' + esc(it.type) + '</span>' +
        '<span class="sc-pos">' + it.rect[0] + ', ' + it.rect[1] + ' · ' + it.rect[2] + '×' + it.rect[3] +
        (it.visible ? '' : ' · 隐藏') + '</span></button>';
    }
    box.innerHTML = html || '<div class="sc-empty">没有可操作的元素</div>';
    var sel = $('sc-sel');
    if (sel) sel.textContent = uiControlSel ? ('已选：' + uiControlSel) : '未选择';
    var btns = box.querySelectorAll('.sc-elem');
    for (var j=0;j<btns.length;j++){
      btns[j].addEventListener('click', function(){
        uiControlSel = this.getAttribute('data-key');
        renderControl();
      });
    }
  }

  function bindControlBtns(){
    var btns = document.querySelectorAll('#triton-desktop [data-ui]');
    for (var i=0;i<btns.length;i++){
      btns[i].addEventListener('click', function(){
        var act = this.getAttribute('data-ui');
        if (act === 'resetAll'){ applyUiCommands([{ target: '*', action: 'reset' }]); return; }
        if (!uiControlSel){ toast('先在列表里点一个元素'); return; }
        if (act === 'move') applyUiCommands([{ target: uiControlSel, action: 'move', x: 20, y: 20 }]);
        else if (act === 'resize') applyUiCommands([{ target: uiControlSel, action: 'resize', width: 360, height: 420 }]);
        else if (act === 'hide') applyUiCommands([{ target: uiControlSel, action: 'hide' }]);
        else if (act === 'show') applyUiCommands([{ target: uiControlSel, action: 'show' }]);
        else if (act === 'color') applyUiCommands([{ target: uiControlSel, action: 'color', property: 'background', value: '#4d6ef8' }]);
        else if (act === 'reset') applyUiCommands([{ target: uiControlSel, action: 'reset' }]);
      });
    }
  }

  function bindSettings(){
    var rate = $('rate'), val = $('rate-val');
    if (rate && val){
      rate.addEventListener('input', function(){ val.textContent = rate.value; });
    }
    var radios = root.querySelectorAll('input[name="theme"]');
    for (var i=0;i<radios.length;i++){
      radios[i].addEventListener('change', function(){
        root.setAttribute('data-theme', this.value);
        toast('外观已切换为' + ({ system:'跟随系统', light:'浅色', dark:'深色' }[this.value]));
      });
    }
    var sw = $('sw-shell');
    if (sw){
      sw.addEventListener('change', function(){ toast(sw.checked ? '已开启 shell 命令白名单' : '已关闭 shell 命令白名单'); });
    }
    var saveBtn = $('set-save');
    var testBtn = $('set-test');
    var clearBtn = $('set-clear');
    var statusEl = $('set-status');
    function fieldVal(id){ var el = $(id); return el ? el.value.trim() : ''; }
    function setStatus(t){ if (statusEl) statusEl.textContent = t; }
    function saveAgentConfig(payload){
      return fetch('/api/config/llm', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) })
        .then(function(r){ return r.json(); });
    }
    if (saveBtn){
      saveBtn.addEventListener('click', function(){
        var key = fieldVal('set-apikey');
        saveAgentConfig({
          api_key: key || null,
          llm_base_url: fieldVal('set-baseurl') || 'https://api.deepseek.com',
          llm_model: fieldVal('set-model') || 'deepseek-chat'
        })
          .then(function(d){ toast('Agent API 配置已保存'); setStatus(d.has_api_key ? '已配置 Key' : '未配置 Key'); })
          .catch(function(){ toast('保存失败，请确认服务在运行'); });
      });
    }
    if (testBtn){
      testBtn.addEventListener('click', function(){
        setStatus('正在测试…');
        fetch('/api/config/llm/test', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
          api_key: fieldVal('set-apikey') || null,
          llm_base_url: fieldVal('set-baseurl') || null,
          llm_model: fieldVal('set-model') || null
        })})
          .then(function(r){ return r.json(); })
          .then(function(d){ setStatus(d.message || (d.ok ? '连接成功' : '连接失败')); toast(d.ok ? '连接成功' : '连接失败'); })
          .catch(function(){ setStatus('测试失败：无法连接服务'); });
      });
    }
    if (clearBtn){
      clearBtn.addEventListener('click', function(){
        saveAgentConfig({ api_key:'' })
          .then(function(){ var k = $('set-apikey'); if (k) k.value = ''; toast('已清除 Key'); setStatus('未配置 Key'); })
          .catch(function(){ toast('清除失败'); });
      });
    }
    loadAgentConfig();
  }

  function loadAgentConfig(){
    var baseEl = $('set-baseurl'), modelEl = $('set-model'), statusEl = $('set-status'), keyEl = $('set-apikey');
    if (!baseEl) return;
    fetch('/api/config/llm')
      .then(function(r){ return r.json(); })
      .then(function(d){
        baseEl.value = d.llm_base_url || 'https://api.deepseek.com';
        modelEl.value = d.llm_model || 'deepseek-chat';
        if (statusEl) statusEl.textContent = d.has_api_key ? '已配置 Key' : '未配置 Key';
        if (keyEl) keyEl.placeholder = d.has_api_key ? 'sk-...（已保存，留空保持不变）' : 'sk-...';
      })
      .catch(function(){ if (statusEl) statusEl.textContent = '读取失败：服务未连接'; });
  }

  var registryApps = [];
  var SYSTEM_APPS = [
    { id:'control', name:'系统控制', icon:'sliders-horizontal', color:'purple', kind:'control' },
    { id:'settings', name:'设置', icon:'settings', color:'gray', kind:'settings' }
  ];
  var APPS = SYSTEM_APPS.slice();
  var DOCK_APPS = [];

  function toAppEntry(a){
    return {
      id:a.id,
      name:a.name,
      icon:a.icon || 'package',
      color:kindColor(a.kind),
      kind:a.kind || 'generic',
      description:a.description || '',
      entry:a.entry || '',
      window:a.window || null
    };
  }

  function kindColor(kind){
    if (kind==='music') return 'pink';
    if (kind==='workspace') return 'blue';
    if (kind==='qomochat') return 'green';
    if (kind==='python') return 'py';
    if (kind==='pythonterm') return 'term';
    if (kind==='html') return 'purple';
    if (kind==='generic') return 'purple';
    return 'gray';
  }

  function appIdByKind(kind){
    for (var i=0;i<registryApps.length;i++){
      if (registryApps[i].kind===kind) return registryApps[i].id;
    }
    for (var j=0;j<APPS.length;j++){
      if (APPS[j].kind===kind) return APPS[j].id;   // 系统应用（系统控制 / 设置）
    }
    return null;
  }

  function musicAppId(){
    return appIdByKind('music');
  }

  function loadApps(){
    return fetch('/api/apps')
      .then(function(r){ return r.json(); })
      .then(function(d){
        registryApps = (d && d.apps) || [];
        DOCK_APPS = registryApps.map(toAppEntry);
        APPS = DOCK_APPS.slice();
        for (var i=0;i<SYSTEM_APPS.length;i++){
          var s = SYSTEM_APPS[i];
          var dup = false;
          for (var j=0;j<APPS.length;j++){ if (APPS[j].id===s.id || APPS[j].kind===s.kind){ dup = true; break; } }
          if (!dup) APPS.push(s);
        }
        renderDock();
        renderLaunchpad();
      })
      .catch(function(){
        registryApps = [];
        DOCK_APPS = [];
        APPS = SYSTEM_APPS.slice();
        renderDock();
        renderLaunchpad();
      });
  }

  function winTemplate(app){
    var body = '';
    if (app.kind==='workspace'){
      body = '<div class="fm" id="fm">' +
        '<div class="fm-bar">' +
        '<button type="button" class="mini-btn" id="fm-back" title="返回上级目录">←</button>' +
        '<span class="fm-path" id="fm-path">workspace</span>' +
        '<button type="button" class="mini-btn" id="fm-refresh">刷新</button>' +
        '<input type="text" class="fm-name-input" id="fm-name" placeholder="新建名称" aria-label="新建名称">' +
        '<button type="button" class="mini-btn" id="fm-new-file">新建文件</button>' +
        '<button type="button" class="mini-btn" id="fm-new-dir">新建文件夹</button>' +
        '</div>' +
        '<div class="fm-head" id="fm-head"><span>名称</span><span>大小</span><span>修改时间</span></div>' +
        '<div class="fm-list" id="fm-list"></div>' +
        '<div class="fm-editor" id="fm-editor" hidden>' +
        '<div class="fm-editor-bar">' +
        '<button type="button" class="mini-btn" id="fm-editor-back">← 返回</button>' +
        '<span class="fm-editor-name" id="fm-editor-name"></span>' +
        '<button type="button" class="mini-btn primary" id="fm-save">保存</button>' +
        '<button type="button" class="mini-btn" id="fm-run">运行</button>' +
        '<button type="button" class="mini-btn" id="fm-agent">交给 AI</button>' +
        '</div>' +
        '<textarea id="fm-text" spellcheck="false" placeholder="文件内容…"></textarea>' +
        '<pre class="fm-out" id="fm-out" hidden></pre>' +
        '</div>' +
        '<div class="fm-menu" id="fm-menu" hidden>' +
        '<button type="button" data-act="open">打开</button>' +
        '<button type="button" data-act="rename">重命名</button>' +
        '<button type="button" data-act="delete">删除</button>' +
        '</div>' +
        '</div>';
    } else if (app.kind==='music'){
      body = '<div class="music-list" id="music-list"></div>' +
        '<div class="music-player">' +
        '<div class="mp-info"><b id="mp-title">未播放</b><span id="mp-artist" class="m-art"></span></div>' +
        '<div class="mp-prog"><div class="mp-bar" id="mp-bar"></div></div>' +
        '<div class="mp-meta">' +
        '<button type="button" class="mini-btn" id="mp-prev">上一首</button>' +
        '<button type="button" class="mini-btn primary" id="mp-toggle">播放</button>' +
        '<button type="button" class="mini-btn" id="mp-next">下一首</button>' +
        '<span id="mp-dur">--:-- / --:--</span></div>' +
        '</div>';
    } else if (app.kind==='python'){
      body = '<div class="py-root">' +
        '<aside class="py-side">' +
          '<div class="py-tabs">' +
            '<button type="button" class="py-tab active" data-tab="scripts"><i data-lucide="file-code-2" aria-hidden="true"></i>脚本</button>' +
            '<button type="button" class="py-tab" data-tab="env"><i data-lucide="cpu" aria-hidden="true"></i>环境</button>' +
          '</div>' +
          '<div class="py-pane" id="py-pane-scripts">' +
            '<div class="py-dir" id="py-dir-name">脚本目录</div>' +
            '<div class="py-tree" id="py-tree"></div>' +
            '<div class="py-new">' +
              '<input type="text" id="py-new-name" placeholder="new_script.py" aria-label="新脚本文件名">' +
              '<button type="button" class="mini-btn" id="py-new-btn">新建</button>' +
            '</div>' +
          '</div>' +
          '<div class="py-pane" id="py-pane-env" hidden>' +
            '<div class="py-env" id="py-env"></div>' +
            '<div class="py-libs-head"><span>已安装库 <b id="py-lib-count">–</b></span>' +
              '<input type="text" id="py-lib-search" placeholder="筛选…" aria-label="筛选已安装库"></div>' +
            '<div class="py-libs" id="py-libs"></div>' +
          '</div>' +
        '</aside>' +
        '<section class="py-main">' +
          '<div class="py-toolbar">' +
            '<button type="button" class="mini-btn primary" id="py-run"><i data-lucide="play" aria-hidden="true"></i>运行</button>' +
            '<button type="button" class="mini-btn" id="py-stop" disabled><i data-lucide="square" aria-hidden="true"></i>停止</button>' +
            '<button type="button" class="mini-btn" id="py-save"><i data-lucide="save" aria-hidden="true"></i>保存</button>' +
            '<button type="button" class="mini-btn" id="py-del" title="删除当前脚本"><i data-lucide="trash-2" aria-hidden="true"></i>删除</button>' +
            '<button type="button" class="mini-btn" id="py-target" aria-pressed="true">运行→终端</button>' +
            '<span class="py-file" id="py-file">未选择脚本</span>' +
            '<span class="py-state" id="py-state">就绪</span>' +
          '</div>' +
          '<div class="py-editor">' +
            '<pre class="py-gutter" id="py-gutter">1</pre>' +
            '<textarea id="py-code" spellcheck="false" aria-label="代码编辑器" placeholder="从左侧选择一个脚本，或在下方输入文件名点「新建」…"></textarea>' +
          '</div>' +
          '<div class="py-console-head">' +
            '<span class="py-console-title"><i data-lucide="terminal" aria-hidden="true"></i>输出</span>' +
            '<input type="text" id="py-args" placeholder="运行参数，如 --name Triton" aria-label="运行参数">' +
            '<button type="button" class="mini-btn" id="py-stdin-btn">标准输入</button>' +
            '<button type="button" class="mini-btn" id="py-clear"><i data-lucide="eraser" aria-hidden="true"></i>清空</button>' +
          '</div>' +
          '<textarea id="py-stdin" class="py-stdin" spellcheck="false" placeholder="程序的标准输入（stdin）…" aria-label="标准输入" hidden></textarea>' +
          '<pre class="py-console" id="py-console"></pre>' +
        '</section>' +
      '</div>';
    } else if (app.kind==='pythonterm'){
      body = '<div class="pt-root">' +
        '<div class="pt-toolbar">' +
          '<span class="pt-dot" id="pt-dot" aria-hidden="true"></span>' +
          '<span class="pt-label" id="pt-label">未启动</span>' +
          '<select id="pt-mode" aria-label="启动模式">' +
            '<option value="script-repl">运行脚本 + 交互</option>' +
            '<option value="repl">纯 REPL</option>' +
            '<option value="script">只跑脚本</option>' +
          '</select>' +
          '<select id="pt-script" aria-label="选择脚本"></select>' +
          '<input type="text" id="pt-args" placeholder="参数" aria-label="运行参数">' +
          '<button type="button" class="mini-btn primary" id="pt-start">启动</button>' +
          '<button type="button" class="mini-btn" id="pt-stop" disabled>停止</button>' +
          '<button type="button" class="mini-btn" id="pt-clear">清空</button>' +
        '</div>' +
        '<div class="pt-screen" id="pt-screen" tabindex="0"></div>' +
        '<div class="pt-input">' +
          '<span class="pt-prompt" id="pt-prompt">&gt;&gt;&gt;</span>' +
          '<input type="text" id="pt-line" autocomplete="off" spellcheck="false" disabled ' +
            'placeholder="输入 Python 代码，或程序 input() 需要的内容，Enter 发送">' +
          '<button type="button" class="mini-btn primary" id="pt-send" disabled>发送</button>' +
        '</div>' +
        '<div class="pt-foot" id="pt-hint">点「启动」开一个真实 Python 会话：输出实时刷新，输入实时送达；↑↓ 翻历史，Ctrl+C 清空当前输入。</div>' +
      '</div>';
    } else if (app.kind==='control'){
      body = '<div class="sc-head">界面元素（实时快照）<span id="sc-sel">未选择</span></div><div id="sc-elems"></div>' +
        '<div class="sc-actions">' +
        '<button type="button" class="mini-btn" data-ui="move">移到左上</button>' +
        '<button type="button" class="mini-btn" data-ui="resize">缩放 360×420</button>' +
        '<button type="button" class="mini-btn" data-ui="hide">隐藏</button>' +
        '<button type="button" class="mini-btn" data-ui="show">显示</button>' +
        '<button type="button" class="mini-btn" data-ui="color">改色</button>' +
        '<button type="button" class="mini-btn" data-ui="reset">还原此元素</button>' +
        '<button type="button" class="mini-btn primary" data-ui="resetAll">全部还原</button></div>' +
        '<div class="sc-tip">也可以直接跟小 AI 说：把任务栏隐藏 / 把发送按钮移到左上角 / 聊天区背景换成淡紫…</div>';
    } else if (app.kind==='settings'){
      body = '<div class="about-card">' +
        '<div class="about-head">' +
          '<div class="about-logo"><i data-lucide="sparkles" aria-hidden="true"></i></div>' +
          '<div class="about-meta">' +
            '<div class="about-name">Triton AIOS<span class="about-badge">PREVIEW</span></div>' +
            '<div class="about-version">v0.1.0 · VeliUI Edition</div>' +
          '</div>' +
        '</div>' +
        '<p class="about-desc">一个 AI Native 桌面：对话、技能、可生成应用，都在同一个界面里完成。</p>' +
        '<div class="about-tags"><span>AI 对话</span><span>4 个内置技能</span><span>应用生成</span><span>联网搜索</span></div>' +
        '<div class="about-foot">© 2026 Triton AIOS · 预览版，欢迎反馈</div>' +
      '</div>' +
      '<div class="set-block"><div class="set-block-title">通用</div>' +
        '<div class="set-row"><span>外观</span><div class="set-opts">' +
        '<label><input type="radio" name="theme" value="system" checked>跟随系统</label>' +
        '<label><input type="radio" name="theme" value="light">浅色</label>' +
        '<label><input type="radio" name="theme" value="dark">深色</label></div></div>' +
        '<div class="set-row"><span>语音播报语速</span><input type="range" id="rate" min="0.5" max="1.5" step="0.05" value="0.9" aria-label="语音播报语速"><b id="rate-val">0.9</b></div>' +
        '<div class="set-row"><label class="sw"><input type="checkbox" id="sw-shell" checked> shell 命令白名单</label><span class="m-art">默认开启</span></div></div>' +
        '<div class="set-block"><div class="set-block-title">Agent API</div>' +
        '<div class="set-field"><label for="set-apikey">API Key</label><input type="password" id="set-apikey" autocomplete="off" placeholder="sk-..." aria-label="API Key"></div>' +
        '<div class="set-field"><label for="set-baseurl">接口地址</label><input type="text" id="set-baseurl" placeholder="https://api.deepseek.com" aria-label="接口地址"></div>' +
        '<div class="set-field"><label for="set-model">模型</label><input type="text" id="set-model" placeholder="deepseek-chat" aria-label="模型"></div>' +
        '<div class="set-actions">' +
        '<button type="button" class="mini-btn primary" id="set-save">保存</button>' +
        '<button type="button" class="mini-btn" id="set-test">测试连接</button>' +
        '<button type="button" class="mini-btn" id="set-clear">清除 Key</button></div>' +
        '<div class="set-status" id="set-status">读取中…</div></div>';
    } else if (app.kind==='qomochat'){
      body = '<div class="qc-root">' +
        '<div class="qc-login" id="qc-login">' +
          '<div class="qc-brand"><i data-lucide="message-circle" aria-hidden="true"></i> QomoChat</div>' +
          '<div class="qc-server" id="qc-server">正在读取服务地址…</div>' +
          '<div class="qc-server-row">' +
            '<input id="qc-server-input" type="text" list="qc-server-list" placeholder="服务器地址，如 http://127.0.0.1:5000" autocomplete="off">' +
            '<button type="button" class="mini-btn" id="qc-server-save">切换</button>' +
          '</div>' +
          '<datalist id="qc-server-list"></datalist>' +
          '<input id="qc-username" type="text" placeholder="用户名" autocomplete="off">' +
          '<input id="qc-password" type="password" placeholder="密码" autocomplete="current-password">' +
          '<button type="button" class="mini-btn primary" id="qc-login-btn">登录</button>' +
          '<div class="qc-status" id="qc-login-status"></div>' +
        '</div>' +
        '<div class="qc-main" id="qc-main" hidden>' +
          '<div class="qc-side">' +
            '<div class="qc-me" id="qc-me"></div>' +
            '<div class="qc-search"><input id="qc-search" type="text" placeholder="UID / 用户名 / 昵称">' +
              '<button type="button" class="mini-btn" id="qc-search-btn">搜索</button></div>' +
            '<ul class="qc-list" id="qc-search-list"></ul>' +
            '<div class="qc-label">好友 <span id="qc-friend-count">0</span></div>' +
            '<ul class="qc-list" id="qc-friends"></ul>' +
            '<div class="qc-foot">' +
              '<button type="button" class="mini-btn" id="qc-server-change">换服务器</button>' +
              '<button type="button" class="mini-btn" id="qc-logout">退出</button>' +
            '</div>' +
          '</div>' +
          '<div class="qc-chat">' +
            '<div class="qc-head"><b id="qc-title">选择好友</b><span class="qc-uid" id="qc-uid"></span>' +
              '<button type="button" class="mini-btn" id="qc-clear" hidden>清空记录</button></div>' +
            '<div class="qc-msgs" id="qc-msgs"><div class="qc-empty">从左侧选择好友开始聊天</div></div>' +
            '<div class="qc-send"><input id="qc-text" type="text" placeholder="输入消息…">' +
              '<button type="button" class="mini-btn primary" id="qc-send-btn">发送</button></div>' +
          '</div>' +
        '</div>' +
      '</div>';
    } else {
      var entry = String(app.entry || '');
      if (app.kind === 'html' || /\.html?$/i.test(entry)){
        // AI 生成 / 用户放入的网页应用：直接 iframe 承载，等价于系统应用
        var src = '/api/apps/' + encodeURIComponent(app.id) + '/asset/' + encodeURIComponent(entry || 'index.html');
        body = '<div class="app-host"><iframe class="app-frame" data-src="'+esc(src)+'" src="'+esc(src)+'" ' +
          'sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads" ' +
          'referrerpolicy="no-referrer" title="'+esc(app.name)+'"></iframe></div>';
      } else {
        body = '<div class="generic-app">' +
          '<span class="ga-ic"><i data-lucide="'+esc(app.icon || 'package')+'" aria-hidden="true"></i></span>' +
          '<p class="ga-name">'+esc(app.name)+'</p>' +
          '<p class="ga-desc">'+esc(app.description || '（没有描述）')+'</p>' +
          '<p class="ga-hint">这个应用来自 apps/ 目录（kind: '+esc(app.kind)+'），本机还没有对应的窗口渲染器。</p>' +
          '</div>';
      }
    }
    return '<div class="win-bar">' +
      '<span class="wb-ic"><i data-lucide="'+app.icon+'" aria-hidden="true"></i></span>' +
      '<span class="wb-title">'+esc(app.name)+'</span>' +
      '<span class="wb-ctl"><button type="button" class="wb-min" aria-label="最小化"></button><button type="button" class="wb-close" aria-label="关闭"></button></span></div>' +
      '<div class="win-body'+(app.kind==='music' ? ' music-body':'')+'">' + body + '</div>' +
      '<span class="win-rz" data-dir="n" aria-hidden="true"></span>' +
      '<span class="win-rz" data-dir="s" aria-hidden="true"></span>' +
      '<span class="win-rz" data-dir="e" aria-hidden="true"></span>' +
      '<span class="win-rz" data-dir="w" aria-hidden="true"></span>' +
      '<span class="win-rz corner" data-dir="ne" aria-hidden="true"></span>' +
      '<span class="win-rz corner" data-dir="nw" aria-hidden="true"></span>' +
      '<span class="win-rz corner" data-dir="se" aria-hidden="true"></span>' +
      '<span class="win-rz corner" data-dir="sw" aria-hidden="true"></span>';
  }

  var wins = [], zTop = 30, offset = 0;
  var winSizes = {};
  var MIN_WIN_W = 300, MIN_WIN_H = 220;

  function applyWinRect(st, left, top, w, h){
    st.el.style.left = Math.round(left) + 'px';
    st.el.style.top = Math.round(top) + 'px';
    st.el.style.width = Math.round(w) + 'px';
    st.el.style.height = Math.round(h) + 'px';
  }

  function rememberWinSize(st){
    var rect = st.el.getBoundingClientRect();
    winSizes[st.id] = { w: Math.round(rect.width), h: Math.round(rect.height) };
  }

  function openApp(id){
    var app = null;
    for (var i=0;i<APPS.length;i++){ if (APPS[i].id===id){ app = APPS[i]; break; } }
    if (!app) return;
    var existing = null;
    for (var j=0;j<wins.length;j++){ if (wins[j].id===id){ existing = wins[j]; break; } }
    if (existing){
      if (existing.minimized){ existing.minimized = false; existing.el.style.display = 'flex'; }
      focusWin(existing);
      return existing;
    }
    var el = document.createElement('div');
    el.className = 'win' + (app.kind === 'qomochat' ? ' qc-win' : '') +
      (app.kind === 'python' ? ' py-win' : '') + (app.kind === 'pythonterm' ? ' pt-win' : '') +
      (app.kind === 'html' ? ' app-win' : '') + (app.kind === 'settings' ? ' set-win' : '');
    el.setAttribute('data-ui-key', 'win:' + app.id);
    el.innerHTML = winTemplate(app);
    // 网页应用支持在 manifest 里声明建议窗口尺寸
    if (app.kind === 'html' && app.window && app.window.width){
      var wantW = Math.max(MIN_WIN_W, Math.min(1200, Number(app.window.width) || 680));
      var wantH = Math.max(MIN_WIN_H, Math.min(900, Number(app.window.height) || 520));
      el.style.width = wantW + 'px';
      el.style.height = wantH + 'px';
    }
    root.appendChild(el);
    refreshIcons();          // 窗口模板里的 lucide 图标（如设置里的品牌标识）
    var st = { id:id, el:el, minimized:false };
    wins.push(st);
    var w = el.offsetWidth || 380, h = el.offsetHeight || 300;
    var base = offset++ % 5;
    var left = 30 + base*32, top = 40 + base*26;
    left = Math.min(Math.max(8,left), root.clientWidth - w - 8);
    top = Math.min(Math.max(10,top), root.clientHeight - h - 76);
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    var saved = winSizes[id];
    if (saved){
      var cw = Math.min(saved.w, root.clientWidth - left - 6);
      var ch = Math.min(saved.h, root.clientHeight - top - 60);
      if (cw >= MIN_WIN_W && ch >= MIN_WIN_H) applyWinRect(st, left, top, cw, ch);
    }
    bindWin(st);
    focusWin(st);
    afterOpen(app);
    reapplyUiOverrides();
    postUiSnapshot();
    toast('已打开 ' + app.name);
    return st;
  }
  function focusWin(st){
    if (hideAllWins) setHideAllWins(false, true);   // 有窗口被激活就自动退出「全部隐藏」
    st.minimized = false;
    st.el.style.display = 'flex';
    zTop++;
    st.el.style.zIndex = zTop;
    for (var i=0;i<wins.length;i++){ wins[i].el.classList.toggle('focused', wins[i]===st); }
    updateDockDots();
  }
  function closeWin(st){
    st.el.remove();
    wins = wins.filter(function(w){ return w!==st; });
    updateDockDots();
    postUiSnapshot();
  }
  function updateDockDots(){
    var items = document.querySelectorAll('#triton-desktop .dock-item[data-app]');
    for (var i=0;i<items.length;i++){
      var id = items[i].getAttribute('data-app');
      var open = false;
      for (var j=0;j<wins.length;j++){ if (wins[j].id===id){ open = true; break; } }
      items[i].classList.toggle('active', open);
    }
  }

  function findWin(id){
    for (var i=0;i<wins.length;i++){ if (wins[i].id===id) return wins[i]; }
    return null;
  }

  function minimizeWin(st){
    st.minimized = true;
    st.el.style.display = 'none';
    st.el.classList.remove('focused');
    updateDockDots();
    postUiSnapshot();
  }

  /* 任务栏图标点击：没开就开、最小化就还原、已经在最前面就最小化（和系统任务栏一致） */
  function toggleAppFromDock(id){
    var st = findWin(id);
    if (!st){ openApp(id); return; }
    if (hideAllWins) setHideAllWins(false, true);    // 有任何窗口被召回，就先退出「全部隐藏」
    if (st.minimized){ focusWin(st); return; }
    if (st.el.classList.contains('focused')){ minimizeWin(st); return; }
    focusWin(st);
  }

  /* ===== 隐藏所有窗口（只留 AI 对话窗口） ===== */
  var hideAllWins = false;

  function setHideAllWins(on, silent){
    hideAllWins = !!on;
    root.classList.toggle('hide-wins', hideAllWins);
    var btn = $('btn-hide-wins');
    if (btn){
      btn.setAttribute('aria-pressed', hideAllWins ? 'true' : 'false');
      btn.setAttribute('data-tooltip', hideAllWins ? '显示所有窗口' : '隐藏所有窗口（再点一下恢复）');
      btn.innerHTML = '<i data-lucide="' + (hideAllWins ? 'eye' : 'eye-off') + '" aria-hidden="true"></i>';
      refreshIcons();
    }
    if (silent){
      postUiSnapshot();
      reapplyUiOverrides();
      return;
    }
    if (hideAllWins){
      var n = 0;
      for (var i=0;i<wins.length;i++){ if (!wins[i].minimized) n++; }
      toast(n ? ('已隐藏 ' + n + ' 个窗口，窗口仍在运行') : '当前没有打开的窗口');
    } else {
      toast('窗口已恢复');
    }
    postUiSnapshot();
    reapplyUiOverrides();
  }


  /* ===== AI 对话窗口：拖动 + 改大小（切对话 / 刷新都保持同一尺寸） ===== */
  var chatBoxKey = 'triton.chatBox';
  var CHAT_MIN_W = 320;
  var CHAT_MIN_H = 240;
  var chatBox = (function(){
    try {
      var saved = JSON.parse(localStorage.getItem(chatBoxKey) || 'null');
      if (saved && saved.width > 200 && saved.height > 150) return saved;
    } catch(e){}
    return null;
  })();

  function chatEl(){ return document.querySelector('#triton-desktop .chat'); }

  function currentChatRect(){
    var el = chatEl();
    if (!el) return null;
    var r = el.getBoundingClientRect();
    var rb = root.getBoundingClientRect();
    return { left: Math.round(r.left - rb.left), top: Math.round(r.top - rb.top),
             width: Math.round(r.width), height: Math.round(r.height) };
  }

  function clampChatRect(rect){
    var maxW = Math.max(CHAT_MIN_W, root.clientWidth - 12);
    var maxH = Math.max(CHAT_MIN_H, root.clientHeight - 12);
    var w = Math.min(Math.max(CHAT_MIN_W, Math.round(rect.width || CHAT_MIN_W)), maxW);
    var h = Math.min(Math.max(CHAT_MIN_H, Math.round(rect.height || CHAT_MIN_H)), maxH);
    var left = Math.min(Math.max(6, Math.round(rect.left || 6)), Math.max(6, root.clientWidth - w - 6));
    var top = Math.min(Math.max(6, Math.round(rect.top || 6)), Math.max(6, root.clientHeight - h - 6));
    return { left: left, top: top, width: w, height: h };
  }

  /* 把「用户设定的框」画到屏幕上；桌面变小只是临时收一下，不改设定值 */
  function paintChatBox(){
    var el = chatEl();
    if (!el) return;
    if (!chatBox){
      el.classList.remove('floating');
      el.style.left = el.style.top = el.style.width = el.style.height = '';
      return;
    }
    var r = clampChatRect(chatBox);
    el.classList.add('floating');
    el.style.left = r.left + 'px';
    el.style.top = r.top + 'px';
    el.style.width = r.width + 'px';
    el.style.height = r.height + 'px';
  }

  function setChatBox(rect, persist){
    // 存的就是最终显示的框（不会出现「存了 606、实际只显示 596」这种不一致）
    chatBox = rect ? clampChatRect(rect) : null;
    paintChatBox();
    if (persist === false) return;
    try {
      if (chatBox) localStorage.setItem(chatBoxKey, JSON.stringify(chatBox));
      else localStorage.removeItem(chatBoxKey);
    } catch(e){}
  }

  /* 从默认的居中自适应布局切成固定框（拖动 / 缩放的第一步） */
  function ensureChatBox(){
    if (!chatBox){
      var r = currentChatRect();
      if (r) setChatBox(r, false);
    }
    return chatBox;
  }

  function resetChatBox(){
    setChatBox(null, true);
    toast('AI 窗口已恢复默认大小');
  }

  /* 八向缩放：拖 .chat-rz 把手 */
  function bindChatResize(){
    var el = chatEl();
    if (!el) return;
    var handles = el.querySelectorAll('.chat-rz');
    for (var i=0;i<handles.length;i++){
      handles[i].addEventListener('pointerdown', function(e){
        if (typeof e.button === 'number' && e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        var dir = this.getAttribute('data-dir') || '';
        var start = ensureChatBox();
        if (!start) return;
        var startX = e.clientX, startY = e.clientY;
        var target = chatEl();
        if (target) target.classList.add('dragging');
        function onMove(ev){
          var dx = ev.clientX - startX, dy = ev.clientY - startY;
          var box = { left: start.left, top: start.top, width: start.width, height: start.height };
          if (dir.indexOf('e') >= 0) box.width = start.width + dx;
          if (dir.indexOf('s') >= 0) box.height = start.height + dy;
          if (dir.indexOf('w') >= 0){ box.width = start.width - dx; box.left = start.left + dx; }
          if (dir.indexOf('n') >= 0){ box.height = start.height - dy; box.top = start.top + dy; }
          if (box.width < CHAT_MIN_W){
            box.width = CHAT_MIN_W;
            if (dir.indexOf('w') >= 0) box.left = start.left + start.width - CHAT_MIN_W;
          }
          if (box.height < CHAT_MIN_H){
            box.height = CHAT_MIN_H;
            if (dir.indexOf('n') >= 0) box.top = start.top + start.height - CHAT_MIN_H;
          }
          setChatBox(box, false);
        }
        function onUp(){
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
          var t = chatEl();
          if (t) t.classList.remove('dragging');
          setChatBox(chatBox, true);        // 松手才落盘
          postUiSnapshot();
        }
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
      });
    }
  }

  function bindChatDrag(){
    var el = chatEl();
    if (!el) return;
    paintChatBox();                         // 开局恢复上次的尺寸与位置
    var head = el.querySelector('.chat-head');
    if (!head) return;
    head.addEventListener('pointerdown', function(e){
      if (typeof e.button === 'number' && e.button !== 0) return;
      if (e.target.closest('button') || e.target.closest('.chat-rz')) return;
      var start = ensureChatBox();
      if (!start) return;
      var startX = e.clientX, startY = e.clientY;
      var moved = false;
      el.classList.add('dragging');
      function onMove(ev){
        if (!moved && Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) < 3) return;
        moved = true;
        setChatBox({
          left: start.left + (ev.clientX - startX),
          top: start.top + (ev.clientY - startY),
          width: start.width,
          height: start.height
        }, false);
      }
      function onUp(){
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        el.classList.remove('dragging');
        if (moved){ setChatBox(chatBox, true); postUiSnapshot(); }
      }
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      e.preventDefault();
    });
    head.addEventListener('dblclick', function(e){
      if (e.target.closest('button')) return;
      if (chatBox) resetChatBox();
    });
  }

  function clampOpenWindows(){
    for (var i=0;i<wins.length;i++){
      var st = wins[i];
      if (st.minimized) continue;
      var rect = st.el.getBoundingClientRect();
      var rootRect = root.getBoundingClientRect();
      var left = rect.left - rootRect.left;
      var top = rect.top - rootRect.top;
      var w = rect.width, h = rect.height;
      var maxW = root.clientWidth - 12;
      var maxH = root.clientHeight - 70;
      var minW = Math.min(MIN_WIN_W, maxW);
      var minH = Math.min(MIN_WIN_H, maxH);
      var changed = false;
      if (w > maxW){ w = maxW; changed = true; }
      if (h > maxH){ h = maxH; changed = true; }
      if (w < minW){ w = minW; changed = true; }
      if (h < minH){ h = minH; changed = true; }
      if (left + w > root.clientWidth - 6){ left = root.clientWidth - 6 - w; changed = true; }
      if (top + h > root.clientHeight - 60){ top = root.clientHeight - 60 - h; changed = true; }
      if (left < 6){ left = 6; changed = true; }
      if (top < 8){ top = 8; changed = true; }
      if (changed) applyWinRect(st, left, top, w, h);
    }
    reapplyUiOverrides();   // 小 AI 明确指定过的位置/尺寸优先，别被"防跑出屏幕"的钳位冲掉
  }

  var resizeTimer = null;
  window.addEventListener('resize', function(){
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function(){
      clampOpenWindows();
      paintChatBox();          // 桌面变大/变小：按设定值重新摆放（不覆盖用户设的尺寸）
      postUiSnapshot();
      var items = document.querySelectorAll('#triton-desktop .dock .dock-item');
      for (var i=0;i<items.length;i++){ items[i].style.transform = ''; items[i].style.zIndex = ''; }
    }, 80);
  });
  function bindWin(st){
    var bar = st.el.querySelector('.win-bar');
    bar.addEventListener('pointerdown', function(e){
      if (e.target.closest('.wb-ctl')) return;
      focusWin(st);
      var rect = st.el.getBoundingClientRect();
      var dx = e.clientX - rect.left, dy = e.clientY - rect.top;
      function onMove(ev){
        var x = ev.clientX - dx, y = ev.clientY - dy;
        x = Math.min(Math.max(6,x), root.clientWidth - st.el.offsetWidth - 6);
        y = Math.min(Math.max(8,y), root.clientHeight - st.el.offsetHeight - 70);
        st.el.style.left = x + 'px';
        st.el.style.top = y + 'px';
      }
      function onUp(){
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      }
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
    bar.addEventListener('click', function(e){
      if (e.target.closest('.wb-min')){ st.minimized = true; st.el.style.display = 'none'; updateDockDots(); }
      if (e.target.closest('.wb-close')){ closeWin(st); }
    });
    var handles = st.el.querySelectorAll('.win-rz');
    for (var i=0;i<handles.length;i++){
      handles[i].addEventListener('pointerdown', function(e){
        e.preventDefault();
        focusWin(st);
        var dir = this.getAttribute('data-dir') || '';
        var rect = st.el.getBoundingClientRect();
        var rootRect = root.getBoundingClientRect();
        var sLeft = rect.left - rootRect.left;
        var sTop = rect.top - rootRect.top;
        var sW = rect.width, sH = rect.height;
        var startX = e.clientX, startY = e.clientY;
        var minW = Math.min(MIN_WIN_W, root.clientWidth - 12);
        var minH = Math.min(MIN_WIN_H, root.clientHeight - 70);
        function onMove(ev){
          var dx = ev.clientX - startX, dy = ev.clientY - startY;
          var left = sLeft, top = sTop, w = sW, h = sH;
          if (dir.indexOf('e') >= 0) w = sW + dx;
          if (dir.indexOf('s') >= 0) h = sH + dy;
          if (dir.indexOf('w') >= 0){ w = sW - dx; left = sLeft + dx; }
          if (dir.indexOf('n') >= 0){ h = sH - dy; top = sTop + dy; }
          if (w < minW){ w = minW; if (dir.indexOf('w') >= 0) left = sLeft + sW - minW; }
          if (h < minH){ h = minH; if (dir.indexOf('n') >= 0) top = sTop + sH - minH; }
          if (left < 6){ w -= (6 - left); left = 6; }
          if (top < 8){ h -= (8 - top); top = 8; }
          var maxW = root.clientWidth - 6 - left;
          var maxH = root.clientHeight - 60 - top;
          if (w > maxW) w = maxW;
          if (h > maxH) h = maxH;
          applyWinRect(st, left, top, w, h);
        }
        function onUp(){
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
          rememberWinSize(st);
        }
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
      });
    }
  }
  /* ===== QomoChat 客户端窗口 ===== */
  var qcState = { user:null, friend:null, timer:null, lastSeq:0, ids:{}, bound:{} };

  function qcApi(path, opts){
    opts = opts || {};
    opts.headers = Object.assign({'Content-Type':'application/json'}, opts.headers || {});
    return fetch(path, opts).then(function(r){
      return r.json().then(function(d){
        if (!r.ok){
          var msg = (d && (d.detail || (d.error && d.error.message))) || ('HTTP ' + r.status);
          throw new Error(msg);
        }
        return d;
      });
    });
  }

  function qcStopPoll(){ if (qcState.timer){ clearInterval(qcState.timer); qcState.timer = null; } }

  function qcBindOnce(id, ev, fn){
    if (qcState.bound[id]) return;
    var el = $(id);
    if (!el) return;
    qcState.bound[id] = true;
    el.addEventListener(ev, fn);
  }

  function qcInit(){
    qcState.bound = {};
    qcState.user = null;
    qcState.friend = null;
    qcState.lastSeq = 0;
    qcState.ids = {};
    qcStopPoll();
    qcBindOnce('qc-login-btn', 'click', qcDoLogin);
    qcBindOnce('qc-server-save', 'click', qcSaveServer);
    qcBindOnce('qc-server-change', 'click', qcChangeServer);
    qcBindOnce('qc-logout', 'click', qcDoLogout);
    qcBindOnce('qc-search-btn', 'click', qcSearch);
    qcBindOnce('qc-send-btn', 'click', qcSend);
    qcBindOnce('qc-clear', 'click', qcClear);
    qcBindOnce('qc-search', 'keydown', function(e){ if (e.key==='Enter'){ e.preventDefault(); qcSearch(); } });
    qcBindOnce('qc-text', 'keydown', function(e){ if (e.key==='Enter'){ e.preventDefault(); qcSend(); } });
    qcRefreshStatus();
  }

  function qcFillServerList(d){
    var servers = (d && d.recent_servers) || [];
    if (d && d.base_url && servers.indexOf(d.base_url) < 0) servers = [d.base_url].concat(servers);
    var dl = $('qc-server-list');
    if (dl) dl.innerHTML = servers.map(function(s){ return '<option value="' + esc(s) + '"></option>'; }).join('');
    var inp = $('qc-server-input');
    if (inp && document.activeElement !== inp) inp.value = (d && d.base_url) || '';
  }

  function qcRefreshStatus(){
    return qcApi('/api/qomochat/status').then(function(d){
      var s = $('qc-server');
      if (s){
        s.textContent = (d.base_url || '') + (d.online ? ' · 在线' : ' · 离线') +
          (d.env_override ? '（受环境变量覆盖）' : '');
      }
      qcFillServerList(d);
      if ($('qc-login-status')) $('qc-login-status').textContent = d.error || '';
      if (d.logged_in && d.user) qcEnter(d.user); else qcShowLogin();
    }).catch(function(e){
      if ($('qc-login-status')) $('qc-login-status').textContent = e.message;
    });
  }

  function qcShowLogin(){
    qcStopPoll();
    if ($('qc-main')) $('qc-main').hidden = true;
    if ($('qc-login')) $('qc-login').hidden = false;
  }

  function qcDoLogin(){
    var u = $('qc-username').value.trim();
    var p = $('qc-password').value;
    if (!u || !p){ $('qc-login-status').textContent = '请输入用户名和密码'; return; }
    $('qc-login-status').textContent = '登录中…';
    qcApi('/api/qomochat/login', { method:'POST', body: JSON.stringify({username:u, password:p}) })
      .then(function(res){ $('qc-password').value = ''; qcEnter(res.user); })
      .catch(function(e){ $('qc-login-status').textContent = e.message; });
  }

  function qcDoLogout(){
    qcApi('/api/qomochat/logout', { method:'POST' }).then(function(){
      qcState.user = null;
      qcState.friend = null;
      qcShowLogin();
    }).catch(function(e){ toast(e.message); });
  }

  function qcSaveServer(){
    var url = $('qc-server-input').value.trim();
    if (!url){ $('qc-login-status').textContent = '请输入服务器地址'; return; }
    $('qc-login-status').textContent = '正在切换服务器…';
    qcApi('/api/qomochat/server', { method:'POST', body: JSON.stringify({ base_url:url }) })
      .then(function(d){
        qcState.user = null;
        qcState.friend = null;
        toast('已切换到 ' + d.saved_base_url);
        qcShowLogin();
        qcRefreshStatus();
      })
      .catch(function(e){ $('qc-login-status').textContent = e.message; });
  }

  function qcChangeServer(){
    qcShowLogin();
    var inp = $('qc-server-input');
    if (inp){ inp.focus(); inp.select(); }
  }

  function qcEnter(user){
    qcState.user = user;
    $('qc-login').hidden = true;
    $('qc-main').hidden = false;
    $('qc-me').innerHTML = '<b>' + esc(user.nickname) + '</b><span class="qc-uid">UID ' + esc(user.uid) + '</span>';
    qcLoadFriends();
  }

  function qcLoadFriends(){
    qcApi('/api/qomochat/friends').then(function(d){
      var list = (d && d.friends) || [];
      $('qc-friend-count').textContent = list.length;
      var box = $('qc-friends');
      box.innerHTML = '';
      if (!list.length){
        box.innerHTML = '<li class="qc-empty">还没有好友，可搜索 UID 添加</li>';
        return;
      }
      list.forEach(function(f){
        var li = document.createElement('li');
        li.className = 'qc-friend' + (qcState.friend && qcState.friend.uid === f.uid ? ' active' : '');
        li.innerHTML = '<b>' + esc(f.nickname) + '</b><span>UID ' + esc(f.uid) + '</span>';
        li.addEventListener('click', function(){ qcOpen(f); });
        box.appendChild(li);
      });
    }).catch(function(e){ toast('QomoChat：' + e.message); });
  }

  function qcSearch(){
    var q = $('qc-search').value.trim();
    if (!q) return;
    qcApi('/api/qomochat/search?q=' + encodeURIComponent(q)).then(function(d){
      var box = $('qc-search-list');
      box.innerHTML = '';
      ((d && d.users) || []).forEach(function(u){
        var li = document.createElement('li');
        li.className = 'qc-friend';
        li.innerHTML = '<b>' + esc(u.nickname) + '</b><span>UID ' + esc(u.uid) + '</span>';
        var add = document.createElement('button');
        add.className = 'mini-btn';
        add.textContent = u.is_friend ? '已是好友' : '添加';
        if (!u.is_friend){
          add.addEventListener('click', function(){
            qcApi('/api/qomochat/friends', { method:'POST', body: JSON.stringify({ uid: u.uid }) })
              .then(function(){ toast('已添加 ' + u.nickname); box.innerHTML=''; qcLoadFriends(); })
              .catch(function(e){ toast(e.message); });
          });
        } else { add.disabled = true; }
        li.appendChild(add);
        box.appendChild(li);
      });
    }).catch(function(e){ toast(e.message); });
  }

  function qcOpen(friend){
    qcState.friend = friend;
    qcState.lastSeq = 0;
    qcState.ids = {};
    $('qc-title').textContent = friend.nickname;
    $('qc-uid').textContent = 'UID ' + friend.uid;
    $('qc-clear').hidden = false;
    qcStopPoll();
    qcApi('/api/qomochat/conversation/' + encodeURIComponent(friend.uid) + '/messages?after_seq=0')
      .then(function(d){
        var box = $('qc-msgs');
        box.innerHTML = '';
        var list = (d && d.messages) || [];
        list.forEach(qcRenderMsg);
        if (!list.length) box.innerHTML = '<div class="qc-empty">还没有消息</div>';
        if (list.length) qcState.lastSeq = list[list.length-1].seq;
        qcLoadFriends();
        qcState.timer = setInterval(qcPoll, 3000);
      })
      .catch(function(e){ toast(e.message); });
  }

  function qcPoll(){
    if (!qcState.friend) return;
    qcApi('/api/qomochat/conversation/' + encodeURIComponent(qcState.friend.uid) + '/messages?after_seq=' + qcState.lastSeq)
      .then(function(d){
        ((d && d.messages) || []).forEach(function(m){
          if (m.seq > qcState.lastSeq) qcState.lastSeq = m.seq;
          qcRenderMsg(m);
        });
      }).catch(function(){});
  }

  function qcRenderMsg(m){
    if (m.id != null && qcState.ids[m.id]) return;
    if (m.id != null) qcState.ids[m.id] = true;
    var box = $('qc-msgs');
    if (box.querySelector('.qc-empty')) box.innerHTML = '';
    var mine = qcState.user && Number(m.sender_id) === Number(qcState.user.id);
    var div = document.createElement('div');
    div.className = 'qc-msg' + (mine ? ' mine' : '');
    if (m.kind === 'file' && m.payload && m.payload.file){
      var f = m.payload.file;
      var dl = '/api/qomochat/download?url=' + encodeURIComponent(f.url || '') +
        '&name=' + encodeURIComponent(f.name || '');
      div.innerHTML = '<a class="qc-file-link" href="' + esc(dl) + '" download>📄 ' +
        esc(f.name) + ' · ' + esc(fmtSize(f.size)) + ' ⬇</a>' +
        (m.payload.text ? '<span class="qc-note">' + esc(m.payload.text) + '</span>' : '');
    } else {
      div.textContent = (m.payload && m.payload.text) || '';
    }
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function qcSend(){
    if (!qcState.friend) { toast('请先选择好友'); return; }
    var text = $('qc-text').value.trim();
    if (!text) return;
    $('qc-text').value = '';
    qcApi('/api/qomochat/conversation/' + encodeURIComponent(qcState.friend.uid) + '/messages', {
      method:'POST', body: JSON.stringify({ text:text })
    }).then(function(d){ qcRenderMsg(d.message); })
      .catch(function(e){ toast(e.message); });
  }

  function qcClear(){
    if (!qcState.friend) return;
    if (!confirm('确定清空与 ' + qcState.friend.nickname + ' 的聊天记录吗？')) return;
    qcApi('/api/qomochat/conversation/' + encodeURIComponent(qcState.friend.uid) + '/clear', { method:'POST' })
      .then(function(d){
        qcState.lastSeq = 0;
        qcState.ids = {};
        $('qc-msgs').innerHTML = '<div class="qc-empty">已清空聊天记录</div>';
        toast(d.deleted_from_server > 0 ? '双方都已清空，服务器已删除' : '已清空（对方清空后服务器删除）');
      })
      .catch(function(e){ toast(e.message); });
  }

  /* ===== Python 运行时窗口：脚本列表 / 编辑器 / 控制台 / 环境 ===== */
  var pyState = {
    files: [], current: null, info: null, libs: [],
    runId: null, ctrl: null, running: false, dirty: false,
    target: 'inline'   // inline = 本窗口控制台；term = 独立「Python 终端」窗口
  };

  var PY_TEMPLATE = [
    '# -*- coding: utf-8 -*-',
    '"""在 Triton AIOS 的 Python 运行时里新建的脚本。"""',
    '',
    '',
    'def main():',
    '    print("Hello, Triton AIOS!")',
    '',
    '',
    'if __name__ == "__main__":',
    '    main()',
    ''
  ].join('\n');

  function pyApi(path, opts){
    opts = opts || {};
    if (opts.body) opts.headers = Object.assign({'Content-Type':'application/json'}, opts.headers || {});
    return fetch('/api/python' + path, opts).then(function(r){
      return r.json().then(function(d){
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function pyStatus(text, cls){
    var el = $('py-state');
    if (el){ el.textContent = text; el.className = 'py-state' + (cls ? ' ' + cls : ''); }
  }

  function pyInit(){
    pyState.files = [];
    pyState.current = null;
    pyState.info = null;
    pyState.libs = [];
    pyState.runId = null;
    pyState.ctrl = null;
    pyState.running = false;
    pyState.dirty = false;
    pyState.target = ptAvailable() ? 'term' : 'inline';
    var code = $('py-code');
    if (code) code.value = '';
    var label = $('py-file');
    if (label){ label.textContent = '未选择脚本'; label.removeAttribute('title'); }
    var args = $('py-args'); if (args) args.value = '';
    var stdin = $('py-stdin'); if (stdin){ stdin.value = ''; stdin.hidden = true; }
    var stdinBtn = $('py-stdin-btn'); if (stdinBtn) stdinBtn.classList.remove('active');
    pyConsoleClear();
    pyGutter();
    pyStatus('就绪');
    pyUpdateTarget();
    pySetTab('scripts');
    pyBind();
    pyLoadFiles();
    pyLoadInfo();
  }

  function pyBind(){
    var on = function(id, fn){ var el = $(id); if (el) el.addEventListener('click', fn); };
    on('py-run', pyRun);
    on('py-stop', pyStop);
    on('py-save', pySave);
    on('py-del', pyDelete);
    on('py-clear', pyConsoleClear);
    on('py-new-btn', pyNew);
    on('py-target', function(){
      pyState.target = (pyState.target === 'term') ? 'inline' : 'term';
      pyUpdateTarget();
    });
    on('py-stdin-btn', function(){
      var box = $('py-stdin');
      if (!box) return;
      box.hidden = !box.hidden;
      this.classList.toggle('active', !box.hidden);
      if (!box.hidden) box.focus();
    });
    var nameInput = $('py-new-name');
    if (nameInput){
      nameInput.addEventListener('keydown', function(e){ if (e.key==='Enter'){ e.preventDefault(); pyNew(); } });
    }
    var code = $('py-code');
    if (code){
      code.addEventListener('input', function(){ pyState.dirty = true; pyGutter(); pyStatus('未保存', 'warn'); });
      code.addEventListener('scroll', pySyncGutter);
      code.addEventListener('keydown', function(e){
        if (!(e.ctrlKey || e.metaKey)) return;
        if (e.key === 'Enter'){ e.preventDefault(); pyRun(); }
        else if (String(e.key).toLowerCase() === 's'){ e.preventDefault(); pySave(); }
      });
    }
    var search = $('py-lib-search');
    if (search) search.addEventListener('input', pyRenderLibs);
    var tabs = document.querySelectorAll('.py-tab');
    for (var i=0;i<tabs.length;i++){
      tabs[i].addEventListener('click', function(){ pySetTab(this.getAttribute('data-tab')); });
    }
    refreshIcons();
  }

  function pySetTab(tab){
    var tabs = document.querySelectorAll('.py-tab');
    for (var i=0;i<tabs.length;i++){
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === tab);
    }
    var scripts = $('py-pane-scripts'), env = $('py-pane-env');
    if (scripts) scripts.hidden = (tab !== 'scripts');
    if (env) env.hidden = (tab !== 'env');
  }

  function pyGutter(){
    var code = $('py-code'), gutter = $('py-gutter');
    if (!code || !gutter) return;
    var lines = code.value.split('\n').length;
    var out = '';
    for (var i=1;i<=lines;i++) out += i + '\n';
    gutter.textContent = out;
    pySyncGutter();
  }

  function pySyncGutter(){
    var code = $('py-code'), gutter = $('py-gutter');
    if (code && gutter) gutter.scrollTop = code.scrollTop;
  }

  function pyIconName(f){
    if (f.type === 'dir') return 'folder';
    return f.python ? 'file-code-2' : 'file-text';
  }

  function pyRenderFiles(d){
    pyState.files = (d && d.files) || [];
    var box = $('py-tree');
    if (!box) return;
    var html = '';
    (function walk(items, depth){
      for (var i=0;i<items.length;i++){
        var f = items[i];
        var pad = 'padding-left:' + (6 + depth*12) + 'px;';
        if (f.type === 'dir'){
          html += '<div class="py-node dir" style="'+pad+'"><i data-lucide="folder" aria-hidden="true"></i>' +
            '<span class="nm">'+esc(f.name)+'/</span></div>';
          walk(f.children || [], depth + 1);
        } else {
          html += '<button type="button" class="py-node file'+(f.python ? '' : ' muted')+(pyState.current === f.path ? ' sel' : '')+'"' +
            ' data-path="'+esc(f.path)+'" style="'+pad+'" title="'+esc(f.path)+'">' +
            '<i data-lucide="'+pyIconName(f)+'" aria-hidden="true"></i>' +
            '<span class="nm">'+esc(f.name)+'</span>' +
            (f.python ? '<span class="sz">'+fmtSize(f.size || 0)+'</span>' : '') +
            '</button>';
        }
      }
    })(pyState.files, 0);
    if (!html) html = '<div class="py-empty">脚本目录还是空的<br>输入文件名点「新建」</div>';
    box.innerHTML = html;
    refreshIcons();
    var nodes = box.querySelectorAll('.py-node.file');
    for (var j=0;j<nodes.length;j++){
      nodes[j].addEventListener('click', function(){ pyOpen(this.getAttribute('data-path')); });
    }
    var head = $('py-dir-name');
    if (head){
      var dir = (d && d.dir) || '';
      var short = dir ? dir.split(/[\\/]/).filter(Boolean).pop() : 'workspace';
      head.textContent = short + '/ · ' + ((d && d.count) || 0) + ' 个文件';
      head.title = dir;
    }
  }

  function pyLoadFiles(){
    return pyApi('/files')
      .then(function(d){ pyRenderFiles(d); return d; })
      .catch(function(e){ pyStatus('目录读取失败', 'err'); throw e; });
  }

  function pyMarkSelected(path){
    var nodes = document.querySelectorAll('.py-node.file');
    for (var i=0;i<nodes.length;i++){
      nodes[i].classList.toggle('sel', nodes[i].getAttribute('data-path') === path);
    }
  }

  function pyOpen(path){
    return pyApi('/file?path=' + encodeURIComponent(path)).then(function(d){
      pyState.current = d.path;
      pyState.dirty = false;
      var code = $('py-code');
      if (code) code.value = d.content;
      var label = $('py-file');
      if (label){ label.textContent = d.path; label.title = d.path + ' · ' + fmtSize(d.size); }
      pyGutter();
      pyMarkSelected(d.path);
      pyStatus('已载入' + (d.truncated ? '（内容过长已截断）' : ''), d.truncated ? 'warn' : '');
      return d;
    }).catch(function(e){ toast(e.message); });
  }

  function pySave(){
    if (!pyState.current){ toast('请先选择一个脚本'); return Promise.resolve(); }
    var code = $('py-code');
    var content = code ? code.value : '';
    pyStatus('保存中…');
    return pyApi('/file', { method:'PUT', body: JSON.stringify({ path: pyState.current, content: content }) })
      .then(function(){
        pyState.dirty = false;
        pyStatus('已保存 · ' + fmtSize(content.length), 'ok');
        return pyLoadFiles();
      })
      .catch(function(e){ pyStatus('保存失败', 'err'); toast(e.message); throw e; });
  }

  function pyNew(){
    var input = $('py-new-name');
    var name = input ? input.value.trim() : '';
    if (!name){ toast('请输入文件名，例如 demo.py'); return; }
    if (name.indexOf('.') < 0) name += '.py';
    pyApi('/new', { method:'POST', body: JSON.stringify({ path: name, content: PY_TEMPLATE }) })
      .then(function(d){
        if (input) input.value = '';
        toast('已新建 ' + d.path + '（含起步模板）');
        return pyLoadFiles().then(function(){ return pyOpen(d.path); });
      })
      .catch(function(e){ toast(e.message); });
  }

  function pyDelete(){
    if (!pyState.current){ toast('请先选择一个脚本'); return; }
    var path = pyState.current;
    if (typeof confirm === 'function' && !confirm('删除 ' + path + ' ？该操作不可撤销。')) return;
    pyApi('/delete', { method:'POST', body: JSON.stringify({ path: path }) })
      .then(function(){
        toast('已删除 ' + path);
        pyState.current = null;
        pyState.dirty = false;
        var code = $('py-code'); if (code) code.value = '';
        var label = $('py-file'); if (label){ label.textContent = '未选择脚本'; label.removeAttribute('title'); }
        pyGutter();
        pyStatus('已删除', 'warn');
        return pyLoadFiles();
      })
      .catch(function(e){ toast(e.message); });
  }

  function pyConsoleClear(){
    var box = $('py-console');
    if (box) box.innerHTML = '';
  }

  /* 控制台/终端共用的写入逻辑：没有换行的片段接在当前行末尾（跟真终端一样），
     这样 input() 的提示符能立刻出现，进度条那种不带换行的输出也能实时刷新。 */
  function writeChunk(box, text, cls, prefix){
    if (!box) return;
    function lineFor(reset){
      var last = box.lastElementChild;
      var line = null;
      if (last && last.getAttribute('data-open') === '1') line = last;
      else {
        line = document.createElement('div');
        line.className = prefix + '-line';
        line.setAttribute('data-open', '1');
        box.appendChild(line);
      }
      // \r 是「回到行首重写」：等真的有新内容时才清，避免 CRLF 把已有文字抹掉
      if (reset || line.getAttribute('data-reset') === '1'){
        line.innerHTML = '';
        line.removeAttribute('data-reset');
      }
      return line;
    }
    var parts = String(text == null ? '' : text).split('\n');
    for (var i=0;i<parts.length;i++){
      var seg = parts[i];
      // 段尾的 \r 属于 CRLF（换行是 \r\n，上面的 split 只切掉了 \n），不是「回到行首重写」
      if (i < parts.length - 1 && seg.charAt(seg.length - 1) === '\r') seg = seg.slice(0, -1);
      var reset = false;
      if (seg.indexOf('\r') >= 0){
        var bits = seg.split('\r');
        seg = bits[bits.length - 1];
        reset = bits.length > 1;
      }
      if (seg !== ''){
        var line = lineFor(reset);
        var span = document.createElement('span');
        span.className = prefix + '-seg' + (cls ? ' ' + cls : '');
        span.textContent = seg;
        line.appendChild(span);
      } else if (reset && box.lastElementChild && box.lastElementChild.getAttribute('data-open') === '1'){
        box.lastElementChild.setAttribute('data-reset', '1');
      }
      if (i < parts.length - 1 && box.lastElementChild){
        box.lastElementChild.setAttribute('data-open', '0');
        box.lastElementChild.removeAttribute('data-reset');
      }
    }
    box.scrollTop = box.scrollHeight;
  }

  function pyWrite(text, cls){ writeChunk($('py-console'), text, cls, 'py'); }

  function pyPrint(text, cls){ pyWrite(String(text == null ? '' : text) + '\n', cls); }

  function pyEvent(ev){
    if (!ev || !ev.type) return;
    if (ev.type === 'start'){
      pyState.runId = ev.run_id || null;
      pyPrint('$ ' + ((ev.cmd || []).join(' ')), 'cmd');
      return;
    }
    if (ev.type === 'stdout'){ pyWrite(String(ev.text || ''), 'out'); return; }
    if (ev.type === 'stderr'){ pyWrite(String(ev.text || ''), 'err'); return; }
    if (ev.type === 'error'){ pyPrint(String(ev.text || ''), 'err'); return; }
    if (ev.type === 'exit'){ pyFinish(ev.code, null, ev.duration); return; }
  }

  function pyRun(){
    if (pyState.running) return;
    if (!pyState.current){ toast('请先选择一个 .py 文件'); return; }
    if (!/\.pyw?$/i.test(pyState.current)){ toast('只能运行 .py 文件'); return; }
    if (pyState.target === 'term' && ptAvailable()){
      var argsEl = $('py-args');
      var args = (argsEl && argsEl.value.trim()) ? argsEl.value.trim().split(/\s+/) : [];
      if (pyState.dirty){
        pySave().then(function(){ pyRunInTerminal(args); }).catch(function(){});
        return;
      }
      pyRunInTerminal(args);
      return;
    }
    if (pyState.dirty){ pySave().then(function(){ pyStartStream(); }).catch(function(){}); return; }
    pyStartStream();
  }

  function pyRunInTerminal(args){
    if (!ptRunScript(pyState.current, args)){ toast('未安装 Python 终端应用'); return; }
    pyConsoleClear();
    pyPrint('▶ ' + pyState.current + ' 已送到「Python 终端」窗口运行（那边可以直接输入程序需要的内容）', 'sys');
    pyStatus('已在终端窗口运行', 'ok');
  }

  function pyUpdateTarget(){
    var btn = $('py-target');
    if (!btn) return;
    var term = (pyState.target === 'term');
    btn.setAttribute('aria-pressed', term ? 'true' : 'false');
    btn.textContent = term ? '运行→终端' : '运行→内嵌';
    btn.title = term
      ? '当前：在独立「Python 终端」窗口里运行（支持交互输入），点击切回本窗口控制台'
      : '当前：在本窗口内嵌控制台里运行（只读输出），点击切到独立终端窗口';
  }

  function pyStartStream(){
    var argsEl = $('py-args');
    var stdinEl = $('py-stdin');
    var args = (argsEl && argsEl.value.trim()) ? argsEl.value.trim().split(/\s+/) : [];
    var stdin = (stdinEl && !stdinEl.hidden && stdinEl.value) ? stdinEl.value : null;
    var payload = { path: pyState.current, args: args, stdin: stdin };
    pyConsoleClear();
    pyPrint('▶ 运行 ' + pyState.current + (args.length ? ' ' + args.join(' ') : '') + '    （Ctrl+Enter 运行 · Ctrl+S 保存）', 'sys');
    pyState.running = true;
    pyState.ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var stopBtn = $('py-stop'); if (stopBtn) stopBtn.disabled = false;
    var runBtn = $('py-run'); if (runBtn) runBtn.disabled = true;
    pyStatus('运行中…', 'run');
    var started = Date.now();
    fetch('/api/python/run/stream', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      body: JSON.stringify(payload),
      signal: pyState.ctrl ? pyState.ctrl.signal : undefined
    }).then(function(res){
      if (!res.ok){
        return res.json().then(function(d){ throw new Error((d && d.detail) || ('HTTP ' + res.status)); });
      }
      if (!res.body || !res.body.getReader){
        return res.text().then(function(text){
          var lines = text.split('\n');
          for (var i=0;i<lines.length;i++){ if (lines[i].trim()) pyEvent(JSON.parse(lines[i])); }
        });
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      function pump(){
        return reader.read().then(function(chunk){
          if (chunk.done){
            if (buf.trim()){ try { pyEvent(JSON.parse(buf)); } catch(e){} }
            return null;
          }
          buf += decoder.decode(chunk.value, { stream:true });
          var parts = buf.split('\n');
          buf = parts.pop();
          for (var i=0;i<parts.length;i++){
            if (!parts[i].trim()) continue;
            try { pyEvent(JSON.parse(parts[i])); } catch(e){}
          }
          return pump();
        });
      }
      return pump();
    }).catch(function(err){
      if (err && err.name === 'AbortError') return;
      pyPrint('✖ ' + ((err && err.message) || err), 'err');
      if (pyState.running) pyFinish(-1, Date.now() - started);
    }).then(function(){
      if (pyState.running) pyFinish(-1, Date.now() - started);
    });
  }

  function pyFinish(code, ms, duration){
    pyState.running = false;
    pyState.runId = null;
    pyState.ctrl = null;
    var stopBtn = $('py-stop'); if (stopBtn) stopBtn.disabled = true;
    var runBtn = $('py-run'); if (runBtn) runBtn.disabled = false;
    if (code === null || code === undefined){
      pyStatus('已停止', 'warn');
      pyPrint('■ 已停止', 'warn');
      return;
    }
    var secs = (duration !== null && duration !== undefined) ? duration : (ms ? (ms / 1000).toFixed(2) : null);
    var tail = secs !== null && secs !== undefined ? ' · ' + secs + 's' : '';
    if (code === 0){
      pyPrint('✔ 退出码 0' + tail, 'ok');
      pyStatus('运行完成', 'ok');
    } else {
      pyPrint('✖ 退出码 ' + code + tail, 'err');
      pyStatus('运行失败（退出码 ' + code + '）', 'err');
    }
    pyLoadFiles();
  }

  function pyStop(){
    if (!pyState.running) return;
    var runId = pyState.runId;
    if (pyState.ctrl){ try { pyState.ctrl.abort(); } catch(e){} }
    if (runId){ pyApi('/stop', { method:'POST', body: JSON.stringify({ run_id: runId }) }).catch(function(){}); }
    pyFinish(null);
  }

  function pyLoadInfo(){
    return pyApi('/info').then(function(info){
      pyState.info = info;
      pyState.libs = info.packages || [];
      var box = $('py-env');
      if (box){
        var plat = String(info.platform || '').split('-')[0] || info.platform;
        box.innerHTML =
          '<div class="py-env-row"><span>解释器</span><b>' + esc(info.implementation) + ' ' + esc(info.version) + '</b></div>' +
          '<div class="py-env-row"><span>平台</span><b>' + esc(plat) + '</b></div>' +
          '<div class="py-env-row"><span>已安装库</span><b>' + info.package_count + ' 个</b></div>' +
          '<div class="py-env-row"><span>单次超时</span><b>' + (info.timeout > 0 ? info.timeout + ' 秒' : '不限时') + '</b></div>' +
          '<div class="py-env-path" title="'+esc(info.executable)+'">'+esc(info.executable)+'</div>' +
          '<div class="py-env-path" title="'+esc(info.dir)+'">脚本目录：'+esc(info.dir)+'</div>';
      }
      var count = $('py-lib-count');
      if (count) count.textContent = info.package_count + ' 个';
      pyRenderLibs();
      pyStatus('就绪 · Python ' + info.version);
      return info;
    }).catch(function(){});
  }

  function pyRenderLibs(){
    var box = $('py-libs');
    if (!box) return;
    var search = $('py-lib-search');
    var kw = (search ? search.value : '').trim().toLowerCase();
    var html = '';
    for (var i=0;i<pyState.libs.length;i++){
      var p = pyState.libs[i];
      if (kw && String(p.name).toLowerCase().indexOf(kw) < 0) continue;
      html += '<div class="py-lib"><span class="nm">' + esc(p.name) + '</span><span class="ver">' + esc(p.version) + '</span></div>';
    }
    box.innerHTML = html || '<div class="py-empty">没有匹配的库</div>';
  }

  /* ===== Python 终端窗口：长驻会话，输出实时、输入实时 ===== */
  var ptState = {
    session: null, after: 0, timer: null, alive: false, drain: 0,
    history: [], histIdx: -1, pending: null, info: null
  };

  function ptApi(path, opts){
    opts = opts || {};
    if (opts.body) opts.headers = Object.assign({'Content-Type':'application/json'}, opts.headers || {});
    return fetch('/api/python' + path, opts).then(function(r){
      return r.json().then(function(d){
        if (!r.ok) throw new Error((d && d.detail) || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function ptAvailable(){ return !!appIdByKind('pythonterm'); }

  function ptClearScreen(){
    var box = $('pt-screen');
    if (box) box.innerHTML = '';
  }

  function ptWrite(text, cls){ writeChunk($('pt-screen'), text, cls, 'pt'); }

  function ptEnableInput(on){
    var line = $('pt-line'), send = $('pt-send');
    if (line) line.disabled = !on;
    if (send) send.disabled = !on;
    if (on && line) line.focus();
  }

  function ptStatus(alive, info){
    if (info) ptState.info = info;
    var dot = $('pt-dot');
    if (dot) dot.classList.toggle('on', !!alive);
    var label = $('pt-label');
    if (label){
      var cur = ptState.info || {};
      if (alive){
        label.textContent = '运行中 · ' + (cur.label || '') + (cur.mode ? ' · ' + cur.mode : '');
      } else if (cur.exit_code !== null && cur.exit_code !== undefined){
        label.textContent = '已结束 · 退出码 ' + cur.exit_code + (cur.label ? ' · ' + cur.label : '');
      } else {
        label.textContent = '未启动';
      }
    }
    var stop = $('pt-stop');
    if (stop) stop.disabled = !alive;
    var start = $('pt-start');
    if (start) start.textContent = ptState.session ? '重启' : '启动';
    ptEnableInput(!!alive);
  }

  function ptStopPoll(){
    if (ptState.timer){ clearInterval(ptState.timer); ptState.timer = null; }
  }

  function ptStartPoll(){
    ptStopPoll();
    ptState.drain = 0;
    ptState.timer = setInterval(ptPoll, 500);
  }

  function ptPoll(){
    if (!ptState.session) return;
    var sid = ptState.session;
    var after = ptState.after;
    ptApi('/sessions/poll?session_id=' + encodeURIComponent(sid) + '&after=' + after)
      .then(function(d){
        if (ptState.session !== sid) return;
        var events = d.events || [];
        for (var i=0;i<events.length;i++){
          var ev = events[i];
          ptWrite(ev.text, ev.type === 'err' ? 'err' : (ev.type === 'exit' ? 'exit' : 'out'));
        }
        ptState.after = d.seq;
        ptState.alive = !!d.alive;
        if (d.alive){
          ptState.drain = 0;
        } else {
          // 进程结束后多兜几轮，把管道里残留的输出收干净
          ptState.drain = (ptState.drain || 0) + 1;
          if (ptState.drain >= 4) ptStopPoll();
        }
        ptStatus(d.alive, d.info);
      })
      .catch(function(){ ptStopPoll(); });
  }

  function ptInit(){
    ptStopPoll();
    ptState.session = null;
    ptState.after = 0;
    ptState.alive = false;
    ptState.drain = 0;
    ptState.history = [];
    ptState.histIdx = -1;
    ptState.info = null;
    ptClearScreen();
    ptWrite('Python 终端 · 真实解释器会话\n', 'sys');
    ptWrite('选一个脚本点「启动」= 跑脚本后继续交互；模式选「纯 REPL」= 直接开交互解释器。\n\n', 'sys');
    ptStatus(false, null);
    ptBind();
    ptModeChanged();
    ptLoadScripts();
    if (ptState.pending){
      ptApplyPending();
    } else {
      ptRefresh();
    }
  }

  function ptBind(){
    var on = function(id, fn){ var el = $(id); if (el) el.addEventListener('click', fn); };
    on('pt-start', ptStart);
    on('pt-stop', ptStop);
    on('pt-clear', function(){ ptClearScreen(); });
    on('pt-send', ptSend);
    var mode = $('pt-mode');
    if (mode) mode.addEventListener('change', ptModeChanged);
    var args = $('pt-args');
    if (args) args.addEventListener('keydown', function(e){ if (e.key === 'Enter'){ e.preventDefault(); ptStart(); } });
    var line = $('pt-line');
    if (line){
      line.addEventListener('keydown', function(e){
        if (e.key === 'Enter'){ e.preventDefault(); ptSend(); return; }
        if (e.key === 'ArrowUp'){
          e.preventDefault();
          if (!ptState.history.length) return;
          ptState.histIdx = (ptState.histIdx < 0) ? ptState.history.length - 1 : Math.max(0, ptState.histIdx - 1);
          line.value = ptState.history[ptState.histIdx] || '';
          return;
        }
        if (e.key === 'ArrowDown'){
          e.preventDefault();
          if (!ptState.history.length || ptState.histIdx < 0) return;
          ptState.histIdx++;
          if (ptState.histIdx >= ptState.history.length){ ptState.histIdx = -1; line.value = ''; }
          else line.value = ptState.history[ptState.histIdx] || '';
          return;
        }
        if (e.ctrlKey && (e.key === 'c' || e.key === 'C')){
          e.preventDefault();
          if (line.value){ line.value = ''; ptWrite('^C\n', 'sys'); }
        }
      });
    }
    var screen = $('pt-screen');
    if (screen) screen.addEventListener('click', function(){ var l = $('pt-line'); if (l && !l.disabled) l.focus(); });
  }

  function ptModeChanged(){
    var mode = $('pt-mode');
    var sel = $('pt-script');
    if (mode && sel) sel.disabled = (mode.value === 'repl');
  }

  function ptLoadScripts(){
    return fetch('/api/python/files').then(function(r){ return r.json(); }).then(function(d){
      var sel = $('pt-script');
      if (!sel) return;
      var current = sel.value;
      var files = [];
      (function walk(items){
        for (var i=0;i<items.length;i++){
          var f = items[i];
          if (f.type === 'dir') walk(f.children || []);
          else if (f.python) files.push(f.path);
        }
      })((d && d.files) || []);
      var html = '<option value="">（未选择脚本）</option>';
      for (var j=0;j<files.length;j++){
        html += '<option value="'+esc(files[j])+'">'+esc(files[j])+'</option>';
      }
      sel.innerHTML = html;
      if (current) sel.value = current;
    }).catch(function(){});
  }

  /* 重开窗口时接回仍在运行的会话（输出重放，变量还在）；已结束的会话不接，避免看到过期现场 */
  function ptRefresh(){
    return ptApi('/sessions').then(function(d){
      var list = d.sessions || [];
      var sess = null;
      for (var i=0;i<list.length;i++){ if (list[i].alive){ sess = list[i]; break; } }
      if (!sess) return null;
      ptState.session = sess.session_id;
      ptState.after = 0;
      ptState.info = sess;
      ptClearScreen();
      ptWrite('● 接回会话 ' + sess.session_id + ' · ' + (sess.label || '') +
        '（仍在运行）\n\n', 'sys');
      ptPoll();
      ptStatus(sess.alive, sess);
      ptStartPoll();
      return sess;
    }).catch(function(){ return null; });
  }

  function ptStart(){
    var modeEl = $('pt-mode');
    var mode = modeEl ? modeEl.value : 'script-repl';
    var sel = $('pt-script');
    var path = sel ? sel.value : '';
    var argsEl = $('pt-args');
    var args = (argsEl && argsEl.value.trim()) ? argsEl.value.trim().split(/\s+/) : [];
    if (mode !== 'repl' && !path){ toast('请先选择要运行的脚本'); return; }
    var previous = ptState.session;
    if (previous){
      ptApi('/sessions/stop', { method:'POST', body: JSON.stringify({ session_id: previous }) }).catch(function(){});
    }
    ptStopPoll();
    ptClearScreen();
    ptState.session = null;
    ptState.after = 0;
    ptWrite('▶ 启动 ' + (mode === 'repl' ? 'Python REPL' : path + (args.length ? ' ' + args.join(' ') : '')) + '\n', 'sys');
    ptApi('/sessions/start', { method:'POST', body: JSON.stringify({ mode: mode, path: path, args: args }) })
      .then(function(s){
        ptState.session = s.session_id;
        ptState.after = 0;
        ptState.info = s;
        ptWrite('● ' + s.banner + '\n\n', 'sys');
        ptStatus(true, s);
        ptStartPoll();
      })
      .catch(function(e){ toast(e.message); ptStatus(false, null); });
  }

  function ptStop(){
    if (!ptState.session) return;
    var sid = ptState.session;
    var label = $('pt-label');
    if (label) label.textContent = '正在停止…';
    var stop = $('pt-stop');
    if (stop) stop.disabled = true;
    ptApi('/sessions/stop', { method:'POST', body: JSON.stringify({ session_id: sid }) })
      .then(function(){ setTimeout(ptPoll, 250); setTimeout(ptPoll, 900); })
      .catch(function(){});
  }

  function ptSend(){
    var line = $('pt-line');
    if (!line) return;
    var text = line.value;
    if (!ptState.session || !ptState.alive){ toast('会话未运行，请先点「启动」'); return; }
    line.value = '';
    if (text !== '') ptState.history.push(text);
    ptState.histIdx = -1;
    ptWrite(text + '\n', 'in');   // 子进程没有 TTY 不会自己回显，这里补上终端回显
    ptApi('/sessions/input', { method:'POST', body: JSON.stringify({ session_id: ptState.session, text: text }) })
      .then(function(r){
        if (r && r.ok === false){
          toast(r.message || '发送失败');
          ptState.alive = false;
          ptStatus(false, ptState.info);
        }
      })
      .catch(function(e){
        toast(e.message);
        ptState.alive = false;
        ptStatus(false, ptState.info);
      });
  }

  /* 运行时窗口用：把脚本丢进终端窗口里跑（可继续交互） */
  function ptRunScript(path, args){
    if (!ptAvailable()) return false;
    // 同一个脚本已经在跑就只把窗口提到最前，避免重复重启会话
    if (ptState.session && ptState.info && ptState.info.alive && ptState.info.label === path){
      openApp(appIdByKind('pythonterm'));
      return true;
    }
    ptState.pending = { path: path, args: args || [], mode: 'script-repl' };
    openApp(appIdByKind('pythonterm'));
    ptApplyPending();
    return true;
  }

  function ptApplyPending(){
    var p = ptState.pending;
    if (!p) return;
    if (!$('pt-mode') || !$('pt-script')) return;
    ptState.pending = null;
    var modeEl = $('pt-mode');
    if (modeEl) modeEl.value = p.mode || 'script-repl';
    var sel = $('pt-script');
    if (sel && p.path){
      var exists = false;
      for (var i=0;i<sel.options.length;i++){ if (sel.options[i].value === p.path) exists = true; }
      if (!exists){
        var opt = document.createElement('option');
        opt.value = p.path;
        opt.textContent = p.path;
        sel.appendChild(opt);
      }
      sel.value = p.path;
    }
    var argsEl = $('pt-args');
    if (argsEl && p.args && p.args.length) argsEl.value = p.args.join(' ');
    ptModeChanged();
    ptStart();
  }

  function afterOpen(app){
    if (app.kind==='workspace'){
      bindFileApp();
      loadWorkspace();
    }
    if (app.kind==='music'){
      bindAudio();
      loadMusic().then(refreshMusicUi);
      refreshMusicUi();
      var tg = $('mp-toggle');
      if (tg){
        tg.addEventListener('click', function(){
          if (playingIdx < 0){ toast('请先选择一首歌'); return; }
          if (audio && audio.paused){ audio.play().catch(function(){}); } else if (audio) { audio.pause(); }
        });
      }
      var prevBtn = $('mp-prev'), nextBtn = $('mp-next');
      if (prevBtn) prevBtn.addEventListener('click', function(){ if (playingIdx < 0){ toast('请先选择一首歌'); return; } playIndex((playingIdx - 1 + songs.length) % songs.length); });
      if (nextBtn) nextBtn.addEventListener('click', function(){ if (playingIdx < 0){ toast('请先选择一首歌'); return; } playIndex((playingIdx + 1) % songs.length); });
    }
    if (app.kind==='control'){ renderControl(); bindControlBtns(); }
    if (app.kind==='settings'){ bindSettings(); }
    if (app.kind==='qomochat'){ qcInit(); }
    if (app.kind==='python'){ pyInit(); }
    if (app.kind==='pythonterm'){ ptInit(); }
  }

  function renderLaunchpad(){
    var box = $('lp-grid');
    var html = '';
    for (var i=0;i<APPS.length;i++){
      var a = APPS[i];
      html += '<button type="button" class="lp-app" data-app="'+a.id+'" data-ui-key="lp:'+a.id+'">' +
        '<span class="lp-ic ic-'+a.color+'"><i data-lucide="'+a.icon+'" aria-hidden="true"></i></span>' +
        '<span class="lp-name">'+a.name+'</span></button>';
    }
    box.innerHTML = html;
    refreshIcons();
    reapplyUiOverrides();
    var btns = box.querySelectorAll('[data-app]');
    for (var j=0;j<btns.length;j++){
      btns[j].addEventListener('click', function(){ closeLaunchpad(); openApp(this.getAttribute('data-app')); });
    }
  }
  function toggleLaunchpad(){ $('launchpad').classList.toggle('open'); }
  function closeLaunchpad(){ $('launchpad').classList.remove('open'); }

  function openMusicApp(){
    var id = musicAppId();
    if (!id){
      toast('未安装音乐应用（apps/ 下没有音乐文件夹）');
      return null;
    }
    return openApp(id);
  }

  function appNameById(id){
    var i;
    for (i=0;i<APPS.length;i++){ if (APPS[i].id === id) return APPS[i].name; }
    for (i=0;i<DOCK_APPS.length;i++){ if (DOCK_APPS[i].id === id) return DOCK_APPS[i].name; }
    return id;
  }

  /* 小 AI 请求开窗（open_window 工具）：直接替他打开，用户不用手动点 */
  function handleOpenWindow(ev){
    // 优先用后端给的应用 id：生成的应用 kind 都是 html，光靠 kind 会开错窗口
    var kind = ev && (ev.app_id || ev.app) ? String(ev.app_id || ev.app) : '';
    var id = appIdByKind(kind);
    if (!id){
      // 也允许直接按应用 id 打开（例如「AI 写应用」刚生成的应用）
      for (var i=0;i<APPS.length;i++){
        if (APPS[i].id === kind){ id = kind; break; }
      }
    }
    if (!id){
      toast('桌面上没有 kind=' + kind + ' 的应用，打不开');
      return;
    }
    var name = appNameById(id);
    var realKind = kind;
    for (var k=0;k<APPS.length;k++){ if (APPS[k].id === id){ realKind = APPS[k].kind; break; } }
    if (realKind === 'pythonterm' && ev.path){
      // 打开 Python 终端，并直接把这个脚本跑起来（可交互）
      if (ptRunScript(ev.path, ev.args || [])) toast('已为你打开「' + name + '」并运行 ' + ev.path);
      return;
    }
    openApp(id);
    if (realKind === 'python' && ev.path) pyOpen(ev.path);      // 运行时窗口里直接载入脚本
    if (realKind === 'music') refreshMusicUi();
    toast('已为你打开「' + name + '」' + (ev.path ? '：' + ev.path : ''));
  }

  /* 兜底：用户明确说「打开某某窗口」时，前端直接开，不等模型决定（模型偶尔会漏调工具） */
  var WIN_VERB_RE = /(打开|开启|弹出|弹出来|给我开|开一下|调出来|显示出来|我要用|我要自己|我想用|我想自己)/;
  var WIN_TARGETS = [
    { re: /(python\s*终端|终端窗口|py\s*终端|终端)/i, kind: 'pythonterm' },
    { re: /(python\s*运行时|运行时窗口|编辑器窗口|py\s*运行时)/i, kind: 'python' },
    { re: /(音乐(播放器|窗口|应用)?|播放器)/, kind: 'music' },
    { re: /(文件管理器|文件窗口|文件应用)/, kind: 'workspace' },
    { re: /(qomochat|聊天窗口|聊天应用)/i, kind: 'qomochat' }
  ];

  function maybeOpenWindowFromText(text){
    var t = String(text || '');
    if (!t || !WIN_VERB_RE.test(t)) return false;
    if (/(不要|别|无需|不用|取消)\s*(打开|弹出|显示|调出)/.test(t)) return false;   // 否定句不处理
    for (var i=0;i<WIN_TARGETS.length;i++){
      if (!WIN_TARGETS[i].re.test(t)) continue;
      var kind = WIN_TARGETS[i].kind;
      var id = appIdByKind(kind);
      if (!id) return false;
      var m = t.match(/([\w\u4e00-\u9fa5.\/\\-]+\.pyw?)/);
      var path = m ? m[1].replace(/^[\/\\]+/, '') : '';
      var wantsRun = !!(path && /(跑起来|运行|执行|交互|自己跑)/.test(t));
      if (kind === 'pythonterm' && path){
        if (ptRunScript(path, [])) toast('已为你打开「Python 终端」' + (wantsRun ? '并运行 ' + path : ''));
        return true;
      }
      openApp(id);
      if (kind === 'python' && path) pyOpen(path);
      if (kind === 'music') refreshMusicUi();
      toast('已为你打开「' + appNameById(id) + '」' + (path ? '：' + path : ''));
      return true;
    }
    return false;
  }

  function renderDock(){
    var dock = $('dock');
    var html = '';
    html += '<button class="dock-item" type="button" data-app="launchpad" data-ui-key="dock:launchpad" aria-label="启动台" data-tooltip="启动台">' +
      '<span class="d-ic ic-grid"><i data-lucide="layout-grid" aria-hidden="true"></i></span></button>';
    if (DOCK_APPS.length){
      html += '<span class="sep" aria-hidden="true"></span>';
      for (var i=0;i<DOCK_APPS.length;i++){
        var a = DOCK_APPS[i];
        html += '<button class="dock-item" type="button" data-app="'+esc(a.id)+'" data-ui-key="dock:'+esc(a.id)+'" aria-label="'+esc(a.name)+'" data-tooltip="'+esc(a.name)+'">' +
          '<span class="d-ic ic-'+a.color+'"><i data-lucide="'+esc(a.icon)+'" aria-hidden="true"></i></span>' +
          '<span class="dot" aria-hidden="true"></span></button>';
      }
    }
    html += '<span class="sep" aria-hidden="true"></span>' +
      '<button class="dock-item" type="button" id="btn-trash" data-ui-key="dock:trash" aria-label="废纸篓" data-tooltip="废纸篓">' +
      '<span class="d-ic ic-trash"><i data-lucide="trash-2" aria-hidden="true"></i></span></button>';
    dock.innerHTML = html;
    refreshIcons();
    updateDockDots();
    reapplyUiOverrides();
  }

  function bindDock(){
    var dock = $('dock');
    dock.addEventListener('click', function(e){
      var btn = e.target.closest('[data-app],#btn-trash');
      if (!btn) return;
      if (btn.id==='btn-trash'){ toast('废纸篓目前是空的'); return; }
      var app = btn.getAttribute('data-app');
      if (app==='launchpad'){ toggleLaunchpad(); return; }
      toggleAppFromDock(app);
    });
    dock.addEventListener('pointermove', function(e){
      var items = dock.querySelectorAll('.dock-item');
      var rect = dock.getBoundingClientRect();
      // dock 支持横向（底部）和纵向（右侧）两种排布，按实际方向取放大轴
      var vertical = getComputedStyle(dock).flexDirection === 'column';
      var p = vertical ? (e.clientY - rect.top) : (e.clientX - rect.left);
      for (var i=0;i<items.length;i++){
        var el = items[i];
        var r = el.getBoundingClientRect();
        var c = vertical ? (r.top - rect.top + r.height/2) : (r.left - rect.left + r.width/2);
        var d = Math.abs(p - c);
        var s = d < 100 ? 1 + (1 - d/100)*0.42 : 1;
        el.style.transform = 'scale(' + s.toFixed(3) + ')';
        el.style.zIndex = s > 1.02 ? 5 : '';
      }
    });
    dock.addEventListener('pointerleave', function(){
      var items = dock.querySelectorAll('.dock-item');
      for (var i=0;i<items.length;i++){ items[i].style.transform = ''; items[i].style.zIndex = ''; }
      reapplyUiOverrides();   // 鼠标划过会把 hover 缩放写进 transform，这里把小 AI 改过的位移贴回去
    });
  }

  function tick(){
    var d = new Date();
    var hh = ('0'+d.getHours()).slice(-2);
    var mm = ('0'+d.getMinutes()).slice(-2);
    $('clock').textContent = hh + ':' + mm;
    // 启动台的数据锚点：大号时钟
    var lpClock = $('lp-clock'), lpDate = $('lp-date');
    if (lpClock) lpClock.textContent = hh + ':' + mm;
    if (lpDate){
      var wd = ['周日','周一','周二','周三','周四','周五','周六'][d.getDay()];
      lpDate.textContent = (d.getMonth() + 1) + ' 月 ' + d.getDate() + ' 日 · ' + wd;
    }
    // 侧栏的相对时间（刚刚 / N 分钟前 / HH:MM / 昨天）跟着走，不用整表重排
    var whens = document.querySelectorAll('#sb-list .sb-item .sb-when');
    for (var i=0;i<whens.length;i++){
      var item = whens[i].closest('.sb-item');
      var conv = item ? convById(item.getAttribute('data-id')) : null;
      if (conv) whens[i].textContent = convTimeLabel(conv);
    }
  }

  $('btn-more').addEventListener('click', function(){
    var open = $('sidebar').classList.toggle('open');
    $('sb-backdrop').classList.toggle('show', open);
    $('btn-more').setAttribute('aria-expanded', open ? 'true':'false');
  });
  $('btn-hide-wins').addEventListener('click', function(){ setHideAllWins(!hideAllWins); });
  $('sb-backdrop').addEventListener('click', closeSidebar);
  $('btn-new-chat').addEventListener('click', newChat);
  bindSkillUi();
  function submitInput(){
    if (sendingAttachments) return;
    var inp = $('chat-input');
    var v = String(inp.value || '').trim();
    var pending = attachments.slice();
    if (!v && !pending.length) return;
    if (!pending.length){
      inp.value = '';
      sendMessage(v);
      return;
    }
    setSending(true);
    uploadAttachments(pending).then(function(uploaded){
      setSending(false);
      if (!uploaded.length){
        toast('附件上传失败，消息未发送');
        return;
      }
      inp.value = '';
      attachments = [];
      renderAttachments();
      sendMessage(v, uploaded);
    }).catch(function(err){
      setSending(false);
      toast('附件上传失败：' + (err && err.message ? err.message : err));
    });
  }
  $('chat-input').addEventListener('keydown', function(e){
    if (e.key === 'Enter'){
      e.preventDefault();
      submitInput();
    }
  });
  $('send-btn').addEventListener('click', submitInput);
  bindComposerAttachments();
  $('launchpad').addEventListener('click', function(e){ if (e.target===this) closeLaunchpad(); });
  document.addEventListener('keydown', function(e){
    if (e.key==='Escape'){ closeLaunchpad(); closeSidebar(); }
  });

  renderConvs();
  renderMsgs();
  scrollMsgs();
  updateChatHeader();
  renderTree();
  updateCount();
  bindAudio();
  renderLaunchpad();
  bindDock();
  bindChatDrag();
  bindChatResize();
  loadSkills();
  loadApps();
  loadConversations();
  loadWorkspace();
  postUiSnapshot();
  setInterval(postUiSnapshot, 20000);
  tick();
  setInterval(tick, 20000);
})();


/* ===== 工具提示（独立页面用） ===== */
(function () {
  'use strict';
  var tip = null;
  function ensureTip() {
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'triton-tip';
      tip.setAttribute('role', 'tooltip');
      document.body.appendChild(tip);
    }
    return tip;
  }
  function showTip(el) {
    var t = ensureTip();
    var txt = el.getAttribute('data-tooltip') || '';
    if (!txt) return;
    t.textContent = txt;
    t.classList.remove('show');
    var r = el.getBoundingClientRect();
    var tr = t.getBoundingClientRect();
    var x = Math.max(8, Math.min(r.left + r.width / 2 - tr.width / 2, document.documentElement.clientWidth - tr.width - 8));
    t.style.left = x + 'px';
    t.style.top = Math.max(8, r.top - tr.height - 10) + 'px';
    t.classList.add('show');
  }
  function hideTip() { if (tip) tip.classList.remove('show'); }
  document.querySelectorAll('[data-tooltip]').forEach(function (el) {
    el.addEventListener('mouseenter', function () { showTip(el); });
    el.addEventListener('mouseleave', hideTip);
    el.addEventListener('focus', function () { showTip(el); });
    el.addEventListener('blur', hideTip);
  });
})();
