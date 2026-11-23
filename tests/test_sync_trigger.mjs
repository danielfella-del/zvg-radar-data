// Offline routing/security matrix using GitHub's own expression evaluator.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
const modulePath=process.env.GITHUB_EXPRESSIONS_MODULE;
const {Lexer,Parser,Evaluator,data}=await import(modulePath?pathToFileURL(modulePath).href:'@actions/expressions');
const workflow=fileURLToPath(new URL('../.github/workflows/sync-zvgpro-db.yml',import.meta.url));
const config=JSON.parse(execFileSync('python3',['-c',
 'import json,sys,yaml;print(json.dumps(yaml.load(open(sys.argv[1]),Loader=yaml.BaseLoader)))',workflow],{encoding:'utf8'}));
const expression=config.jobs.sync.if.trim().slice(3,-2).trim();
const ast=new Parser(new Lexer(expression).lex().tokens,['github'],[]).parse();
function dict(value){return new data.Dictionary(...Object.entries(value).map(([key,v])=>({key,value:typeof v==='object'?dict(v):new data.StringData(v)})));}
const repo='danielfella-del/zvg-radar-data';
const base={ref:'refs/heads/main',repository:repo,event_name:'workflow_run',event:{action:'completed',workflow_run:{
 name:'ZVG Daten stündlich aktualisieren',conclusion:'success',head_branch:'main',head_repository:{full_name:repo},event:'schedule'
}}};
function enabled(g){
 const trigger=config.on.workflow_run;
 if(g.event_name==='workflow_run'&&(!trigger.types.includes(g.event.action)||!trigger.branches.includes(g.event.workflow_run.head_branch)||!trigger.workflows.includes(g.event.workflow_run.name)))return false;
 return new Evaluator(ast,dict({github:g})).evaluate().value;
}
let count=0;
function check(label,edit,wanted){const g=structuredClone(base);edit(g);assert.equal(enabled(g),wanted,label);count++;}
check('successful collector',()=>{},true);
check('successful enrichment',g=>g.event.workflow_run.name='ZVG Dossiers und Medien anreichern',true);
check('manual writer',g=>g.event.workflow_run.event='workflow_dispatch',true);
for(const conclusion of ['failure','cancelled','timed_out','skipped'])check(conclusion,g=>g.event.workflow_run.conclusion=conclusion,false);
check('fork excluded',g=>g.event.workflow_run.head_repository.full_name='fixture/fork',false);
check('feature branch excluded',g=>g.event.workflow_run.head_branch='feature',false);
check('pull request excluded',g=>g.event.workflow_run.event='pull_request',false);
check('unrelated workflow excluded',g=>g.event.workflow_run.name='Untrusted fixture',false);
check('incomplete writer excluded',g=>g.event.action='in_progress',false);
check('timer retained',g=>{g.event_name='schedule';g.event={}},true);
check('manual sync retained',g=>{g.event_name='workflow_dispatch';g.event={}},true);
check('manual sync from branch excluded',g=>{g.event_name='workflow_dispatch';g.ref='refs/heads/feature';g.event={}},false);
check('unconfigured event excluded',g=>g.event_name='push',false);
assert.deepEqual(config.on.schedule,[{cron:'7,22,37,52 * * * *'}]);
assert.equal(config.permissions.contents,'read');
assert.deepEqual(config.concurrency,{group:'zvgpro-mariadb-sync','cancel-in-progress':'false'});
const checkout=config.jobs.sync.steps[0];
assert.equal(checkout.uses,'actions/checkout@v4');
assert.equal(checkout.with.ref,'${{ github.sha }}');
assert.equal(checkout.with['persist-credentials'],'false');
for(const path of ['/data/auctions.json','/.github/scripts/sync-zvgpro-db.py','/collector/publication_policy.py'])assert.ok(checkout.with['sparse-checkout'].includes(path));
assert.ok(config.jobs.sync.steps[1].run.includes('publication_policy.py --check'));
console.log(JSON.stringify({ok:true,routing_cases:count,contracts:['schedule','read permissions','serialization','same immutable snapshot']}));
