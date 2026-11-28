import hashlib, hmac, io, json, os, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from observer import (HELLO_CONTEXT, LIVE_SECONDS, MAX_EVENTS, MAX_LINE, Observer, observe_stdio, read_events, read_registry, workspace_digest)
class ObserverTest(unittest.TestCase):
    def test_projection_session_and_secret(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'.agents/evidence/tools'; p.mkdir(parents=True)
            (p/'s1.jsonl').write_text(json.dumps({'ts':'2026-01-01T00:00:00Z','session':'other','verdict':'DENY','reason':'secret-token-123','tool':'write'})+'\n')
            r=read_events(Path(t)); self.assertEqual(r['events'][0]['session'],'s1'); self.assertNotIn('secret-token-123',json.dumps(r))
    def test_unknown_not_fabricated(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'.agents/evidence/skills'; p.mkdir(parents=True)
            (p/'s2.jsonl').write_text('{"verdict":"SELECTED","skill":"safe-skill"}\n')
            e=read_events(Path(t))['events'][0]; self.assertEqual(e['verdict'],'UNKNOWN'); self.assertEqual(e['skill'],'safe-skill')
    def test_missing_registry(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(read_registry(Path(t))['HOSTS']['state'],'unavailable')
    def test_untrusted_values_not_exposed(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'.agents/evidence/tools'; p.mkdir(parents=True)
            (p/'s.jsonl').write_text(json.dumps({'action':'Bearer secret-token','tool':'safe-tool','duration_seconds':999999999})+'\n')
            e=read_events(Path(t))['events'][0]
            self.assertNotIn('action',e); self.assertNotIn('duration_seconds',e); self.assertEqual(e['tool'],'safe-tool')
    def test_registry_does_not_expose_values(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'registry'; p.mkdir()
            (p/'HOSTS.json').write_text(json.dumps({'hosts':[{'key':'secret-token'}]}))
            r=read_registry(Path(t)); self.assertNotIn('secret-token',json.dumps(r)); self.assertEqual(r['HOSTS']['counts']['hosts'],1)

class ObserverStreamTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'eos-home'
        self.project = self.root / 'project-one'
        self.other = self.root / 'project-two'
        self.engine = self.root / 'engine'
        for path in (self.home, self.project, self.other, self.engine):
            path.mkdir()
        self.now = datetime(2026, 11, 28, 5, 0, 0, tzinfo=timezone.utc)
        self.observer = Observer(self.engine, self.home, [self.project])
        self.log = self.host_log(self.project)

    def host_log(self, project, session='session-a'):
        path = self.home / 'evidence' / 'nexus' / workspace_digest(project) / (session + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def row(self, kind, event_id=None, **fields):
        result = {
            'schema': 1, 'ts': self.now.isoformat(), 'kind': kind,
            'host': 'opencode', **fields,
        }
        if event_id:
            result['id'] = event_id
        return json.dumps(result).encode('utf8') + b'\n'

    @staticmethod
    def append(log, payload):
        with log.open('ab') as writer:
            writer.write(payload)

    def test_native_skill_stages_and_host_only_fields(self):
        self.append(self.log, b''.join([
            self.row('session.started'),
            self.row('skill.selected', skill='code-quality', verdict='IN_USE'),
            self.row('skill.loaded', skill='code-quality'),
            self.row('skill.used', skill='code-quality'),
            self.row('skill.completed', skill='code-quality'),
            self.row('security.denied', reason='this is private', verdict='PASS'),
            self.row('model.reported', model='test-model', provider='test-provider',
                     input_tokens=0, output_tokens=15, cost=0.15, currency='USD',
                     reason='sk-ant-A1234567890ABCDEF', args={'key': 'secret'}),
            self.row('tool.completed', tool='read', model='sk-ant-A1234567890ABCDEF',
                     input_tokens=9999, output='secret', duration_seconds=0.5),
        ]))
        snap = self.observer.scan(self.now)
        self.assertEqual(snap['mode'], 'live')
        self.assertEqual({row['status'] for row in snap['events'] if row['family'] == 'skill'},
                         {'SELECTED', 'LOADED', 'IN_USE', 'COMPLETED'})
        denied = next(row for row in snap['events'] if row['family'] == 'security')
        self.assertEqual(denied['verdict'], 'DENY')
        model = next(row for row in snap['events'] if row['family'] == 'model')
        self.assertEqual((model['input_tokens'], model['output_tokens'], model['cost']), (0, 15, 0.15))
        tool = next(row for row in snap['events'] if row['family'] == 'tool')
        self.assertEqual(tool['duration_seconds'], 0.5)
        self.assertNotIn('input_tokens', tool)
        rendered = json.dumps(snap)
        for secret in ('this is private', 'sk-ant-A1234567890ABCDEF', 'secret', 'session-a'):
            self.assertNotIn(secret, rendered)

    def test_lifecycle_stale_idle_ended_and_restart(self):
        self.append(self.log, self.row('session.started'))
        started = self.observer.scan(self.now)
        self.assertEqual(started['sessions'][0]['mode'], 'live')
        stale = self.observer.scan(self.now + timedelta(seconds=LIVE_SECONDS + 1))
        self.assertEqual(stale['sessions'][0]['mode'], 'stale')
        self.now += timedelta(seconds=LIVE_SECONDS + 2)
        self.append(self.log, self.row('session.idle'))
        self.assertEqual(self.observer.scan(self.now)['sessions'][0]['mode'], 'stale')
        self.append(self.log, self.row('session.resumed'))
        self.assertEqual(self.observer.scan(self.now)['sessions'][0]['mode'], 'live')
        self.append(self.log, self.row('session.ended'))
        fresh = Observer(self.engine, self.home, [self.project]).scan(self.now)
        self.assertEqual(fresh['sessions'][0]['mode'], 'disconnected')
        self.assertEqual(len(fresh['events']), 4)

    def test_same_timestamp_order_and_gate_proof(self):
        self.append(self.log, b''.join([
            self.row('skill.selected', 'z-select', skill='code-quality'),
            self.row('skill.loaded', 'a-load', skill='code-quality'),
            self.row('gate.verified', 'b-missing', verdict='PASS'),
            self.row('gate.verified', 'c-verified', verdict='PASS', artifact_sha256='a' * 64),
        ]))
        snap = self.observer.scan(self.now)
        self.assertEqual([row['status'] for row in snap['events']], ['SELECTED', 'LOADED', 'OBSERVED', 'PASS'])
        self.assertEqual(snap['events'][-1]['artifact_sha256'], 'a' * 64)

    def test_future_receipt_cannot_establish_live(self):
        receipt = (self.now + timedelta(days=1)).isoformat()
        self.append(self.log, self.row('session.started', ts=receipt))
        snap = self.observer.scan(self.now)
        self.assertEqual(snap['mode'], 'disconnected')
        self.assertIn('future_event', snap['issues'])

    def test_project_and_host_session_isolation(self):
        registry = {'workspaces': [{'path': str(self.other), 'name': 'other-project'}]}
        (self.home / 'workspaces.json').write_text(json.dumps(registry), encoding='utf8')
        self.append(self.log, self.row('session.started', 'same-id'))
        self.append(self.host_log(self.other), self.row('session.started', 'same-id', host='claude'))
        snap = self.observer.scan(self.now)
        self.assertEqual(len(snap['projects']), 2)
        self.assertEqual(len(snap['sessions']), 2)
        self.assertEqual(len({row['project'] for row in snap['events']}), 2)
        self.assertEqual(len({row['session'] for row in snap['events']}), 2)
        self.assertEqual(len({row['id'] for row in snap['events']}), 2)

    def test_unauthenticated_workspace_logs_cannot_establish_live_or_gate_pass(self):
        spoof = self.project / '.agents' / 'evidence' / 'nexus' / self.log.name
        spoof.parent.mkdir(parents=True)
        self.append(spoof, self.row('session.started'))
        self.append(spoof, self.row('gate.verified', verdict='PASS'))
        skill_log = spoof.parent.parent / 'skills' / self.log.name
        skill_log.parent.mkdir()
        self.append(skill_log, self.row('skill.used', skill='code-quality', verdict='USED'))
        snap = self.observer.scan(self.now)
        self.assertEqual(snap['mode'], 'stale')
        self.assertEqual([row['status'] for row in snap['events']], ['UNKNOWN'])
        self.assertNotIn('PASS', json.dumps(snap))

    def test_incremental_partial_dedup_and_replacement(self):
        payload = self.row('agent.started', 'agent-start', agent='reviewer')
        self.append(self.log, payload[:len(payload)//2])
        self.assertEqual(self.observer.scan(self.now)['events'], [])
        self.append(self.log, payload[len(payload)//2:])
        first = self.observer.scan(self.now)['events']
        self.assertEqual(len(first), 1)
        self.append(self.log, payload)
        self.assertEqual(self.observer.scan(self.now)['events'], first)
        moved = self.log.with_suffix('.old')
        self.log.rename(moved)
        self.append(self.log, self.row('agent.completed', 'agent-end', agent='reviewer'))
        rotated = self.observer.scan(self.now)
        self.assertEqual({row['status'] for row in rotated['events']}, {'ACTIVE', 'COMPLETED'})
        self.assertIn('log_reset', rotated['issues'])

    def test_bounds_and_loss_reporting(self):
        self.append(self.log, b'x' * (MAX_LINE + 2) + b'\n{invalid}\n')
        self.append(self.log, self.row('telemetry.loss', lost=7))
        snap = self.observer.scan(self.now)
        self.assertEqual(snap['issueCounts']['producer_loss'], 7)
        self.assertIn('malformed_event', snap['issues'])
        self.assertIn('oversized_event', snap['issues'])
        for index in range(3):
            self.append(self.log, b''.join(self.row('tool.completed', f'{index}-{n}', tool='read')
                                           for n in range(MAX_EVENTS // 2)))
            snap = self.observer.scan(self.now)
        self.assertEqual(len(snap['events']), MAX_EVENTS)
        self.assertIn('history_window_full', snap['issues'])

    def test_junction_or_symlink_sources_are_skipped(self):
        destination = self.root / 'elsewhere'
        destination.mkdir()
        redirected = self.project / '.agents'
        try:
            redirected.symlink_to(destination, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'host prevents symbolic link test: {error.winerror if os.name == "nt" else error.errno}')
        payload = destination / 'evidence' / 'tools' / self.log.name
        payload.parent.mkdir(parents=True)
        self.append(payload, self.row('security.denied', verdict='DENY'))
        snap = self.observer.scan(self.now)
        self.assertFalse(snap['events'])
        self.assertIn('read_error', snap['issues'])

    def test_private_stdio_authentication_and_child_shutdown(self):
        token = bytes(range(32))
        expected = hmac.new(token, HELLO_CONTEXT, hashlib.sha256).hexdigest()
        child = subprocess.Popen(
            [sys.executable, '-B', '-u', str(Path(__file__).with_name('observer.py')),
             '--stdio', '--workspace', str(self.project), '--engine', str(self.engine),
             '--eos-home', str(self.home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with child:
            try:
                child.stdin.write(json.dumps({'type': 'hello', 'token': token.hex()}).encode('ascii') + b'\n')
                child.stdin.flush()
                greeting = json.loads(child.stdout.readline())
                self.assertEqual(greeting, {'schema': 1, 'type': 'hello', 'proof': expected})
                snap = json.loads(child.stdout.readline())
                self.assertEqual((snap['type'], snap['mode']), ('snapshot', 'disconnected'))
                child.stdin.close()
                self.assertEqual(child.wait(timeout=5), 0)
            finally:
                if child.poll() is None:
                    child.kill()

    def test_cli_hook_and_reader_agree_on_scope_and_projection(self):
        hook = Path(__file__).resolve().parents[1] / 'engine' / 'tools' / 'nexus_event.py'
        if not hook.is_file():
            self.skipTest('host adapter is not present in this observer package')
        env = {**os.environ, 'EOS_HOME': str(self.home), 'EOS_NEXUS_WORKSPACE': str(self.project)}
        base = {'session_id': 'test-host-session', 'cwd': str(self.project)}
        for event in (
            {**base, 'hook_event_name': 'SessionStart', 'model': 'native-model'},
            {**base, 'hook_event_name': 'PostToolUse', 'tool_name': 'Skill',
             'tool_use_id': 'skill-call', 'tool_input': {'skill': 'safe-skill', 'prompt': 'private-input'}},
            {**base, 'cwd': str(self.other), 'hook_event_name': 'SessionStart', 'model': 'out-of-scope-model'},
        ):
            subprocess.run([sys.executable, '-B', str(hook), 'claude'],
                           input=json.dumps(event).encode('utf8'), env=env, capture_output=True,
                           check=True, timeout=10)
        snap = self.observer.scan()
        self.assertEqual(len(snap['sessions']), 1)
        self.assertEqual(snap['sessions'][0]['host'], 'claude')
        self.assertEqual(snap['sessions'][0]['mode'], 'live')
        self.assertIn(('skill.loaded', 'LOADED'), [(row['kind'], row['status']) for row in snap['events']])
        self.assertNotIn('private-input', json.dumps(snap))
        self.assertNotIn('out-of-scope-model', json.dumps(snap))

    def test_invalid_greeting_has_no_output(self):
        outgoing = io.BytesIO()
        outcome = observe_stdio(self.observer, io.BytesIO(b'{"type":"hello","token":"bad"}\n'),
                                outgoing, 0.25)
        self.assertEqual(outcome, 2)
        self.assertEqual(outgoing.getvalue(), b'')


if __name__ == '__main__':
    unittest.main()
