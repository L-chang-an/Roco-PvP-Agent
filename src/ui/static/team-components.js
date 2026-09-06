"use strict";
/* Pure presentation helpers shared by the editor and read-only advice cards. */
window.TeamComponents = (() => {
  const stats = Object.freeze([
    {cn: '生命', field: 'hp'}, {cn: '物攻', field: 'atk'}, {cn: '魔攻', field: 'sp_atk'},
    {cn: '物防', field: 'def'}, {cn: '魔防', field: 'sp_def'}, {cn: '速度', field: 'speed'}
  ]);
  const statText = values => stats.filter(s => values[s.field] !== undefined).map(s => s.cn + ' ' + values[s.field]).join(' / ');
  const skillNames = skills => (Array.isArray(skills) ? skills : []).map(s => typeof s === 'string' ? s : s?.name || '').filter(Boolean);
  return {stats, statText, skillNames};
})();
