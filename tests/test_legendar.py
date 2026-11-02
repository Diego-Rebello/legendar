"""Testes sem gasto de limite: o modelo é sempre simulado e os textos são sintéticos.

Rodar com: python3 -I tests/test_legendar.py
"""
import contextlib, importlib.util, io, json, os, signal, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location('legendar', Path(__file__).resolve().parent.parent / 'scripts' / 'legendar.py')
lg = importlib.util.module_from_spec(spec)
sys.modules['legendar'] = lg
spec.loader.exec_module(lg)

SRT = """﻿1
00:00:01,000 --> 00:00:03,000
[sighs]

2
00:00:04,000 --> 00:00:06,000
Hello there,
my friend.

3
00:00:07,000 --> 00:00:09,000
<i>We owe 32 dollars.</i>

4
00:00:10,000 --> 00:00:12,000
<i>Sing along.</i>
"""


def cue(lines, timing='00:00:00,000 --> 00:00:10,000'):
    return lg.Cue(1, timing, lines)


def response(text, stop='end_turn'):
    return {'result': text, 'stop_reason': stop, 'usage': {'input_tokens': 10, 'output_tokens': 5},
            'modelUsage': {'claude-haiku': {}}, 'duration_ms': 1, 'total_cost_usd': 0}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.srt = self.dir / 'video.srt'
        self.srt.write_text(SRT, encoding='utf-8')
        patcher = mock.patch.object(lg, 'CACHE_DIR', self.dir / 'cache')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cmd(self, replies, **kw):
        """Roda o pipeline com respostas simuladas e devolve os prompts enviados."""
        prompts, replies, self.efforts, self.systems = [], list(replies), [], []

        def fake(prompt, model, effort, raw_dir, system=None):
            prompts.append(prompt)
            self.efforts.append(effort)
            self.systems.append(system)
            return replies.pop(0)

        args = SimpleNamespace(input=str(self.srt), model='haiku', effort='high', start=0, max_blocks=99, block_size=100, dry_run=False, retry_refused=False)
        vars(args).update(kw)
        func = getattr(args, 'func', lg.cmd_run)
        with mock.patch.object(lg, 'call_model', fake), mock.patch.object(lg, 'check_auth'):
            self.code = func(args)
        return prompts


class ParseAndRender(Base):
    def test_bom_and_lines(self):
        cues = lg.parse_srt(self.srt)
        self.assertEqual([c.id for c in cues], [1, 2, 3, 4])
        self.assertEqual(cues[1].lines, ['Hello there,', 'my friend.'])

    def test_round_trip_keeps_timings_and_text(self):
        cues = lg.parse_srt(self.srt)
        again = self.dir / 'again.srt'
        again.write_text(lg.render_srt(cues, {c.id: c.lines for c in cues}), encoding='utf-8')
        self.assertEqual(lg.parse_srt(again), cues)

    def test_pipe_in_source_aborts(self):
        self.srt.write_text('1\n00:00:01,000 --> 00:00:02,000\na | b\n', encoding='utf-8')
        with self.assertRaises(SystemExit):
            lg.parse_srt(self.srt)


class Response(unittest.TestCase):
    def test_doubt_and_breaks(self):
        found, problems = lg.parse_response('2?\tOlá, | amigo.\n3\t<i>Devemos 32.</i>', [2, 3])
        self.assertEqual(found[2], (['Olá,', 'amigo.'], True))
        self.assertEqual(found[3], (['<i>Devemos 32.</i>'], False))
        self.assertEqual(problems, [])

    def test_ignores_fences_and_reports_noise(self):
        found, problems = lg.parse_response('```\n2\tOlá\nAqui está:\n99\tfora do bloco\n2\tde novo\n```', [2])
        self.assertEqual(found, {2: (['Olá'], False)})
        self.assertEqual(len(problems), 3)


class Validation(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(lg.validate(cue(['<i>Hello.</i>']), ['<i>Olá.</i>']), [])

    def test_lost_italics_and_number(self):
        flags = lg.validate(cue(['<i>We owe 32.</i>']), ['Devemos muito.'])
        self.assertIn('marca <i> 1->0', flags)
        self.assertIn('número perdido', flags)

    def test_long_line_and_speed(self):
        flags = lg.validate(cue(['Hi'], '00:00:00,000 --> 00:00:01,000'), ['x' * 43])
        self.assertIn('linha longa', flags)
        self.assertIn('43 cps', flags)

    def test_untranslated(self):
        self.assertIn('igual ao original', lg.validate(cue(['Hello there']), ['Hello there']))
        self.assertEqual(lg.validate(cue(['Cubano.']), ['Cubano.']), [])


class StripNonSpeech(unittest.TestCase):
    def check(self, lines, expected):
        self.assertEqual(lg.strip_non_speech(lines), expected)

    def test_effect_only_cues_disappear(self):
        for lines in (['[sighs]'], ['- [rain]', '- [thunder]'], ['<i>[sighs]</i>'], ['[humming "A Song', 'by Someone"]'],
                      ['♪ tense music ♪'], ['♪ música de suspense ♪'], ['♪ "A Song"', 'by Someone playing ♪'],
                      ['♪ "Uma Música"', 'de Alguém tocando ♪'], ['♪'], ['- ♪', '- [dialogue inaudible]'],
                      ['- ♪ somber music', '- [chatter]']):
            self.check(lines, [])

    def test_speaker_names(self):
        self.check(['[Ana]', 'Hello, <i>friend.</i>'], ['Hello, <i>friend.</i>'])
        self.check(['[Ana] <i>Two lines', 'of text.</i>'], ['<i>Two lines', 'of text.</i>'])
        self.check(['- Excuse me.', '- [officer] Yes?'], ['- Excuse me.', '- Yes?'])

    def test_effect_inside_dialogue(self):
        self.check(["Can't... [scoffs]", "can't do it."], ["Can't...", "can't do it."])

    def test_lone_dash_goes_away(self):
        self.check(['- [phone chimes]', '- Yo.'], ['Yo.'])
        self.check(['<i>- Sure.</i>', '- [key clicks]'], ['<i>Sure.</i>'])
        self.check(['- ♪ intense music', '<i>- Ten victims</i>'], ['<i>Ten victims</i>'])

    def test_untouched_cues_stay_identical(self):
        for lines in (['- Yes.'], ['- Hi.', '- Hello.'], ['Wait -- what?'], ['Hello there,', 'my friend.']):
            self.check(lines, lines)

    def test_lyrics_disappear(self):
        for lines in (['<i>♪ Sing along ♪</i>'], ['<i>♪ I love this music ♪</i>'], ['<i>♪ Two lines', 'of a song ♪</i>'],
                      ['♪ Plain line', '<i>then italic ♪</i>'], ['- <i>♪ One ♪</i>', '- <i>♪ Two ♪</i>'],
                      ['- ♪ "A Song"', '- <i>♪ Yeah, yeah ♪</i>'], ['- <i>♪ So loud... ♪</i>', '- [whirring]'],
                      ['<i>♪ Three', 'lines', 'long ♪</i>']):
            self.check(lines, [])

    def test_speech_next_to_lyrics_stays(self):
        self.check(['- Incoming.', '- <i>♪ Old song ♪</i>'], ['Incoming.'])
        self.check(['- <i>♪ Old song... ♪</i>', "- Let's go!"], ["Let's go!"])

    def test_italics_spanning_a_removed_line(self):
        self.check(['<i>[Ana]', 'Hello.</i>'], ['<i>Hello.</i>'])


class Pipeline(Base):
    def test_effects_never_reach_model_or_output(self):
        prompts = self.run_cmd([response('2\tOlá, | amigo.\n3\t<i>Devemos 32.</i>\n4\t<i>Cante.</i>')])
        self.assertEqual(len(prompts), 1)
        self.assertNotIn('[sighs]', prompts[0])
        out = lg.parse_srt(self.dir / 'video.pt-BR.srt')
        self.assertEqual([c.id for c in out], [1, 2, 3], 'renumerada, sem buraco')
        self.assertEqual(out[0].lines, ['Olá,', 'amigo.'])
        self.assertEqual([c.timing for c in out], [c.timing for c in lg.parse_srt(self.srt)[1:]])

    def test_resume_only_asks_for_what_is_missing(self):
        first = self.run_cmd([response('2\tOlá\n3\t<i>Devemos 32.</i>')])
        self.assertFalse((self.dir / 'video.pt-BR.srt').exists(), 'SRT com buraco não pode ser gerado')
        second = self.run_cmd([response('4\t<i>Cante.</i>')])
        self.assertEqual(len(first) + len(second), 2)
        translate = second[0].split('TRADUZIR')[1]
        self.assertIn('4\t', translate)
        self.assertNotIn('2\t', translate)
        self.assertIn('=> <i>Devemos 32.</i>', second[0].split('TRADUZIR')[0])
        self.assertEqual(self.run_cmd([]), [], 'tudo em cache: nenhuma chamada')

    def test_context_is_the_five_previous_cues(self):
        self.srt.write_text('\n'.join(f'{i}\n00:00:{i:02},000 --> 00:00:{i:02},500\nline {i}\n' for i in range(1, 10)), encoding='utf-8')
        prompts = self.run_cmd([response('8\toito\n9\tnove')], start=8)
        context, translate = prompts[0].split('TRADUZIR')
        self.assertEqual([x.split('\t')[0] for x in context.split('\n')[1:-1]], ['3', '4', '5', '6', '7'])
        self.assertIn('8\t', translate)

    def test_ledger_counts_every_request_not_just_the_main_one(self):
        reply = response('2\tOlá')
        reply['modelUsage'] = {'claude-haiku': {'inputTokens': 3000, 'outputTokens': 700, 'cacheCreationInputTokens': 90, 'thinkingTokens': 40}}
        self.run_cmd([reply])
        row = lg.Store(lg.speech_cues(lg.parse_srt(self.srt)), 'haiku', 'high').usage()[0]
        self.assertEqual((row['input_tokens'], row['output_tokens'], row['cache_creation_input_tokens'], row['thinking_tokens']), (3000, 700, 90, 40))

    def test_warns_only_when_requested_model_is_absent(self):
        for used, warned in ((['claude-haiku-5-5', 'claude-sonnet-5-5'], False), (['claude-haiku-5-5'], True)):
            reply = response('2\tOlá')
            reply['modelUsage'] = {name: {} for name in used}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.run_cmd([reply], model='sonnet', effort=f'{"low" if warned else "medium"}', max_blocks=1)
            self.assertEqual('AVISO' in out.getvalue(), warned)

    def test_model_swap_ignores_only_the_auxiliary_haiku(self):
        haiku, sonnet, opus = 'claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5'
        for model, used, swapped in (('sonnet', [haiku, sonnet], False), ('sonnet', [haiku], True), ('sonnet', [sonnet, opus], True),
                                     ('sonnet', [], False), ('haiku', [haiku], False), ('haiku', [haiku, sonnet], True), ('haiku', [], False)):
            self.assertEqual(lg.model_swapped(model, used), swapped, (model, used))

    def test_output_lines_read_by_the_app(self):
        """gui/Legendar.swift lê estes textos. Mudou aqui, muda lá."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_cmd([], dry_run=True)
        self.assertRegex(out.getvalue(), r'Blocos nesta rodada: 1\nProgresso: 0 de 3 legendas\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_cmd([response('2\tOlá\n3\t<i>Devemos 32.</i>\n4\t<i>Cante.</i>')])
        self.assertRegex(out.getvalue(), r'(?m)^Traduzindo: bloco 1 de 1, legendas 2-4\nBloco 2-4: ')
        self.assertRegex(out.getvalue(), r'(?m)^Progresso: 3 de 3 legendas\n')
        self.assertIn(f'\nGerado {(self.dir / "video.pt-BR.srt").resolve()}\n', out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_cmd([], dry_run=True)
        self.assertIn(f'\nSaída já existe: {(self.dir / "video.pt-BR.srt").resolve()}\n', out.getvalue())

    def fake_mkvextract(self, lines, code):
        def popen(cmd, **kw):
            Path(cmd[-1].split(':', 1)[1]).write_text('1\n00:00:01,000 --> 00:00:02,000\nHi\n', encoding='utf-8')
            tool = mock.Mock(stdout=lines, returncode=code)
            tool.wait.return_value = code
            return tool
        return mock.patch.object(lg.subprocess, 'Popen', side_effect=popen)

    def mkv(self):
        video = self.dir / 'v.mkv'
        video.write_bytes(b'x')
        return video

    def listing(self, code=0):
        info = {'tracks': [{'id': 2, 'type': 'subtitles', 'properties': {'codec_id': 'S_TEXT/UTF8', 'language': 'eng'}}]}
        return mock.patch.object(lg.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(info), returncode=code))

    def test_extraction_reports_progress_and_failure(self):
        lines = ['Extracting track 2\n'] + [f'#GUI#progress {i}%\n' for i in range(0, 101)]
        out = io.StringIO()
        dest = self.dir / 'x' / 'v.en.srt'
        with self.listing(), self.fake_mkvextract(lines, 0), contextlib.redirect_stdout(out):
            lg.extract_subtitle(Path('v.mkv'), dest)
        self.assertEqual(out.getvalue().splitlines(), [f'Extraindo: {i}%' for i in range(10, 101, 10)] + ['Trilha 2 extraída de v.mkv'])
        self.assertTrue(dest.exists())
        dest.unlink()
        for code in (2, -15):
            with self.listing(), self.fake_mkvextract(['Error: disco cheio\n'], code), self.assertRaisesRegex(SystemExit, 'disco cheio'):
                lg.extract_subtitle(Path('v.mkv'), dest)
            self.assertEqual(list(dest.parent.iterdir()), [], 'extração incompleta não fica no cache')

    def test_extraction_warning_is_not_fatal(self):
        out = io.StringIO()
        dest = self.dir / 'x' / 'v.en.srt'
        with self.listing(1), self.fake_mkvextract(['#GUI#warning Faixa estranha\n'], 1), contextlib.redirect_stdout(out):
            lg.extract_subtitle(Path('v.mkv'), dest)
        self.assertTrue(dest.exists())
        self.assertIn('Faixa estranha', out.getvalue())

    def test_listing_failure_stops(self):
        with self.listing(2), self.assertRaises(SystemExit):
            lg.extract_subtitle(Path('v.mkv'), self.dir / 'x' / 'v.en.srt')

    def test_file_that_is_not_mkv_stops_with_a_message(self):
        with mock.patch.object(lg.subprocess, 'run', return_value=SimpleNamespace(stdout='{"container": {"recognized": false}}', returncode=0)):
            with self.assertRaisesRegex(SystemExit, 'não tem trilha'):
                lg.extract_subtitle(Path('v.mkv'), self.dir / 'x' / 'v.en.srt')

    def test_second_read_reuses_the_extraction(self):
        video = self.mkv()
        with self.listing(), self.fake_mkvextract([], 0) as popen, contextlib.redirect_stdout(io.StringIO()):
            first = lg.read_source(video)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(lg.read_source(video), first)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(out.getvalue(), 'Trilha já extraída de v.mkv\n', 'o app marca a etapa por essa linha')

    def test_changed_or_homonymous_video_is_extracted_again(self):
        video = self.mkv()
        other = self.dir / 'outra'
        other.mkdir()
        (other / 'v.mkv').write_bytes(b'x')
        with self.listing(), self.fake_mkvextract([], 0) as popen, contextlib.redirect_stdout(io.StringIO()):
            lg.read_source(video)
            lg.read_source(other / 'v.mkv')
            self.assertEqual(popen.call_count, 2, 'mesmo nome em outra pasta')
            video.write_bytes(b'xx')
            lg.read_source(video)
            self.assertEqual(popen.call_count, 3, 'arquivo mudou')

    def test_max_blocks_limits_calls(self):
        prompts = self.run_cmd([response('2\tOlá')], block_size=1, max_blocks=1)
        self.assertEqual(len(prompts), 1)

    def test_refusal_is_flagged_not_translated(self):
        self.run_cmd([response('Não posso.', stop='refusal')])
        store = lg.Store(lg.speech_cues(lg.parse_srt(self.srt)), 'haiku', 'high')
        self.assertEqual(store.get(2), {'pt': None, 'flags': ['recusa']})
        self.assertFalse((self.dir / 'video.pt-BR.srt').exists())

    def test_refused_cues_are_not_ready_and_only_return_on_request(self):
        self.run_cmd([response('Não posso.', stop='refusal')])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_cmd([], dry_run=True), [], 'recusa não volta sozinha')
        self.assertIn('0 em cache', out.getvalue())
        self.assertIn('Blocos nesta rodada: 0\nProgresso: 0 de 3 legendas\nRecusadas: 3 legendas, 1 chamada(s) para tentar de novo\n', out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prompts = self.run_cmd([response('2\tOlá\n3\t<i>Devemos 32.</i>\n4\t<i>Cante.</i>')], retry_refused=True)
        self.assertEqual(len(prompts), 1)
        self.assertRegex(out.getvalue(), r'(?m)^Progresso: 3 de 3 legendas\n')
        self.assertTrue((self.dir / 'video.pt-BR.srt').exists())

    def test_truncated_response_drops_last_line(self):
        self.run_cmd([response('2\tOlá\n3\t<i>Dev', stop='max_tokens')])
        store = lg.Store(lg.speech_cues(lg.parse_srt(self.srt)), 'haiku', 'high')
        self.assertIsNotNone(store.get(2))
        self.assertIsNone(store.get(3))

    def test_model_error_stops_without_writing(self):
        def boom(*a):
            raise lg.ModelError('limite')
        args = SimpleNamespace(input=str(self.srt), model='haiku', effort='high', start=0, max_blocks=99, block_size=1, dry_run=False, retry_refused=False)
        with mock.patch.object(lg, 'call_model', boom), mock.patch.object(lg, 'check_auth'):
            self.assertEqual(lg.cmd_run(args), 1)
        self.assertEqual(lg.Store(lg.speech_cues(lg.parse_srt(self.srt)), 'haiku', 'high').entries, {})


class Children(Base):
    def setUp(self):
        super().setUp()
        for name, value in (('child', None), ('stopping', False)):
            patcher = mock.patch.object(lg, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def popen(self, **kw):
        return mock.patch.object(lg.subprocess, 'Popen', **kw)

    def call(self):
        return lg.call_model('p', 'sonnet', 'medium', self.dir / 'raw')

    def test_timeout_and_missing_claude_become_model_error(self):
        slow = mock.Mock(returncode=None)
        slow.communicate.side_effect = [subprocess.TimeoutExpired('claude', 900), ('', '')]
        with self.popen(return_value=slow), self.assertRaisesRegex(lg.ModelError, 'sem resposta'):
            self.call()
        slow.kill.assert_called_once()
        with self.popen(side_effect=FileNotFoundError), self.assertRaisesRegex(lg.ModelError, 'não encontrado'):
            self.call()

    def test_run_reports_where_it_stopped_without_traceback(self):
        args = SimpleNamespace(input=str(self.srt), model='haiku', effort='high', start=0, max_blocks=99, block_size=100, dry_run=False, retry_refused=False)
        err = io.StringIO()
        with self.popen(side_effect=FileNotFoundError), mock.patch.object(lg, 'check_auth'), contextlib.redirect_stderr(err):
            self.assertEqual(lg.cmd_run(args), 1)
        self.assertIn('Parado no bloco 2-4', err.getvalue())

    def test_missing_claude_aborts_auth_check(self):
        with mock.patch.object(lg.subprocess, 'run', side_effect=FileNotFoundError), self.assertRaisesRegex(SystemExit, 'não encontrado'):
            lg.check_auth()

    def test_signal_during_spawn_still_stops_the_child(self):
        spawned = mock.Mock()

        def racing(*a, **kw):
            lg.on_sigterm(signal.SIGTERM, None)  # `child` ainda não existe
            return spawned
        with self.popen(side_effect=racing):
            lg.start_child(['claude'])
        spawned.terminate.assert_called_once()

    def test_after_signal_nothing_new_starts(self):
        lg.on_sigterm(signal.SIGTERM, None)
        with self.popen() as popen, self.assertRaises(SystemExit) as caught:
            lg.start_child(['claude'])
        popen.assert_not_called()
        self.assertEqual(caught.exception.code, 128 + signal.SIGTERM)

    def test_sigterm_reaches_real_child_and_cleans_up(self):
        bin_dir, tmp, pid_file = self.dir / 'bin', self.dir / 'tmp', self.dir / 'pid'
        bin_dir.mkdir()
        tmp.mkdir()
        fake = bin_dir / 'claude'
        fake.write_text('#!/bin/sh\n[ "$1" = auth ] && { echo \'{"authMethod": "claude.ai"}\'; exit 0; }\necho $$ > "$PID_FILE"\nexec sleep 60\n', encoding='utf-8')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'TMPDIR': str(tmp), 'PID_FILE': str(pid_file)}
        script = Path(lg.__file__).resolve()
        p = subprocess.Popen([sys.executable, '-I', str(script), 'run', str(self.srt), '--effort', 'medium'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.kill)
        deadline = time.monotonic() + 15
        while not (pid_file.exists() and pid_file.read_text().strip()):
            self.assertLess(time.monotonic(), deadline, 'o claude falso não começou')
            time.sleep(0.02)
        pid = int(pid_file.read_text())
        p.terminate()
        self.assertEqual(p.wait(15), 128 + signal.SIGTERM)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(list(tmp.iterdir()), [], 'pasta temporária removida')


class Effort(Base):
    def cues(self):
        return lg.speech_cues(lg.parse_srt(self.srt))

    def test_flag_is_always_sent_and_env_cannot_override_it(self):
        done = mock.Mock(returncode=0)
        done.communicate.return_value = (json.dumps(response('x')), '')
        env = {'CLAUDE_CODE_EFFORT_LEVEL': 'low', 'ANTHROPIC_API_KEY': 'x'}
        with mock.patch.dict(lg.os.environ, env), mock.patch.object(lg.subprocess, 'Popen', return_value=done) as run:
            lg.call_model('p', 'sonnet', 'medium', self.dir / 'raw')
        cmd, kw = run.call_args
        self.assertEqual(cmd[0][cmd[0].index('--effort') + 1], 'medium')
        self.assertNotIn('CLAUDE_CODE_EFFORT_LEVEL', kw['env'])
        self.assertNotIn('ANTHROPIC_API_KEY', kw['env'])
        self.assertTrue(list((self.dir / 'raw').glob('*-sonnet-medium.json')))

    def test_required_on_the_command_line(self):
        for argv in (['run', 'x.srt'], ['compare', 'x.srt', 'y.srt'], ['run', 'x.srt', '--effort', 'turbo']):
            with mock.patch.object(sys, 'argv', ['legendar.py'] + argv), mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                lg.main()

    def test_each_effort_has_its_own_cache_and_ledger(self):
        reply = '2\tOlá\n3\t<i>Devemos 32.</i>\n4\t<i>Cante.</i>'
        self.run_cmd([response(reply)], effort='high')
        self.assertEqual(self.efforts, ['high'])
        self.assertEqual(len(self.run_cmd([response(reply)], effort='medium')), 1, 'outro esforço não aproveita o cache')
        self.assertEqual(self.efforts, ['medium'])
        self.assertEqual(self.run_cmd([], effort='high'), [], 'mesmo esforço aproveita')
        for effort in ('high', 'medium'):
            self.assertEqual([r['effort'] for r in lg.Store(self.cues(), 'haiku', effort).usage()], [effort])

    def test_cache_without_effort_is_not_reused(self):
        store = lg.Store(self.cues(), 'haiku', 'high')
        store.dir.mkdir(parents=True)
        (store.dir / f'haiku-{lg.PROMPT_ID}.json').write_text(json.dumps({'2': {'pt': ['velho'], 'flags': []}}), encoding='utf-8')
        (store.dir / 'usage.jsonl').write_text(json.dumps({'model': 'haiku', 'prompt': lg.PROMPT_ID}) + '\n', encoding='utf-8')
        again = lg.Store(self.cues(), 'haiku', 'high')
        self.assertIsNone(again.get(2))
        self.assertEqual(again.usage(), [])


class Review(Base):
    def setUp(self):
        super().setUp()
        self.srt.write_text('\n'.join(f'{i}\n00:00:{i:02},000 --> 00:00:{i + 1:02},000\nline {i}\n' for i in range(1, 7)), encoding='utf-8')
        self.cues = lg.speech_cues(lg.parse_srt(self.srt))
        base = lg.Store(self.cues, 'haiku', 'high')
        for c in self.cues:
            base.put(c.id, [f'linha {c.id}'])
        base.put(3, ['x' * 30])
        base.put(5, ['linha 5'], ['dúvida'])
        base.save()

    def review(self, replies, **kw):
        return self.run_cmd(replies, func=lg.cmd_review, base='haiku:high', by='sonnet:medium', max_blocks=1, **kw)

    def final(self):
        return lg.open_variant(self.cues, 'haiku:high+sonnet:medium')

    def test_sends_only_flagged_cues_with_neighbors_as_context(self):
        prompts = self.review([response('3\ttrês\n5\tcinco')])
        context, todo = prompts[0].split('REVISAR')
        self.assertEqual([x.split('\t')[0] for x in context.split('\n')[1:-1]], ['2', '4', '6'])
        self.assertEqual([x.split('\t')[0] for x in todo.strip().split('\n')], ['3', '5'])
        self.assertIn('30 cps', todo)
        self.assertIn('até 22 caracteres', todo)
        self.assertIn('dúvida', todo)
        self.assertEqual(self.systems, [lg.REVIEW_PROMPT])
        self.assertEqual(self.efforts, ['medium'])

    def test_final_uses_review_and_keeps_the_rest(self):
        self.review([response('3\ttrês\n5\tcinco')])
        final = self.final()
        self.assertEqual(final.get(3)['pt'], ['três'])
        self.assertEqual(final.get(5), {'pt': ['cinco'], 'flags': []})
        self.assertEqual(final.get(2)['pt'], ['linha 2'])

    def test_base_cache_is_not_touched(self):
        self.review([response('3\ttrês\n5\tcinco')])
        self.assertEqual(lg.Store(self.cues, 'haiku', 'high').get(3)['pt'], ['x' * 30])

    def test_resume_only_asks_for_what_is_missing(self):
        self.review([response('3\ttrês')])
        again = self.review([response('5\tcinco')])
        self.assertEqual([x.split('\t')[0] for x in again[0].split('REVISAR')[1].strip().split('\n')], ['5'])
        self.assertEqual(self.review([]), [], 'tudo revisado: nenhuma chamada')

    def test_refused_review_keeps_base_text_with_signal(self):
        self.review([response('Não posso.', stop='refusal')])
        self.assertEqual(self.final().get(5), {'pt': ['linha 5'], 'flags': ['dúvida', 'recusa']})

    def test_review_has_own_ledger_and_other_base_has_own_cache(self):
        reply = response('3\ttrês\n5\tcinco')
        reply['modelUsage'] = {'claude-sonnet': {'inputTokens': 7}}
        self.review([reply])
        self.assertEqual([r['input_tokens'] for r in self.final().review.usage()], [7])
        self.assertEqual(lg.Store(self.cues, 'haiku', 'high').usage(), [])
        self.assertEqual(lg.Reviewed(self.cues, 'haiku:medium', 'sonnet:medium').review.entries, {})

    def test_needs_translation_first(self):
        with self.assertRaises(SystemExit):
            self.run_cmd([], func=lg.cmd_review, base='haiku:low', by='sonnet:medium', max_blocks=1)


class Blind(Base):
    VARIANTS = ['haiku:high', 'sonnet:medium']

    def setUp(self):
        super().setUp()
        self.srt.write_text('\n'.join(f'{i}\n00:00:{i:02},000 --> 00:00:{i:02},500\nline {i}\n' for i in range(1, 21)), encoding='utf-8')
        cues = lg.speech_cues(lg.parse_srt(self.srt))
        self.haiku, self.sonnet = lg.Store(cues, 'haiku', 'high'), lg.Store(cues, 'sonnet', 'medium')
        for c in cues:
            self.haiku.put(c.id, [f'haiku-texto {c.id}'])
            # A legenda 1 fica igual nas duas: não ajuda a comparar.
            self.sonnet.put(c.id, [f'haiku-texto {c.id}' if c.id == 1 else f'sonnet-texto {c.id}'])
        self.haiku.save()
        self.sonnet.save()
        self.out, self.key = self.dir / 'amostra.md', self.dir / 'chave.json'

    def blind(self, **kw):
        args = SimpleNamespace(input=str(self.srt), reference=str(self.srt), variant=self.VARIANTS, sample=5, exclude=None, out=str(self.out), key=str(self.key))
        vars(args).update(kw)
        return lg.cmd_blind(args)

    def test_sheet_has_no_labels_and_key_is_complete(self):
        self.blind(sample=99, exclude='2,3')
        key = json.loads(self.key.read_text(encoding='utf-8'))
        self.assertEqual(sorted(map(int, key)), list(range(4, 21)), 'sem iguais e sem excluídas')
        self.assertTrue(all(sorted(s.values()) == self.VARIANTS for s in key.values()))
        sheet = self.out.read_text(encoding='utf-8')
        for word in ('sonnet:', 'haiku:', 'medium', 'high'):
            self.assertNotIn(word, sheet)

    def test_key_cannot_be_redrawn(self):
        self.blind()
        with self.assertRaises(SystemExit):
            self.blind()

    def test_sides_are_drawn_per_row(self):
        with mock.patch.object(lg.secrets, 'randbits', side_effect=[1, 0]):
            self.blind(sample=2)
        self.assertEqual(sorted(s['A'] for s in json.loads(self.key.read_text(encoding='utf-8')).values()), self.VARIANTS)

    def test_rejects_same_variant_twice(self):
        with self.assertRaises(SystemExit):
            self.blind(variant=['haiku:high', 'haiku:high'])

    def test_unblind_maps_notes_back_to_variants(self):
        self.key.write_text(json.dumps({'2': {'A': 'haiku:high', 'B': 'sonnet:medium'}, '3': {'A': 'sonnet:medium', 'B': 'haiku:high'}}), encoding='utf-8')
        notes = self.dir / 'notas.tsv'
        notes.write_text('# id A B\n2\t3\t1\tcomentário\n3\t0\t2\n', encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lg.cmd_unblind(SimpleNamespace(key=str(self.key), notes=str(notes)))
        self.assertIn('| haiku:high | 2.50 | 2 | 0 |', out.getvalue())
        self.assertIn('| sonnet:medium | 0.50 | 0 | 2 |', out.getvalue())

    def test_unblind_needs_every_note(self):
        self.key.write_text(json.dumps({'2': {'A': 'x:high', 'B': 'y:high'}, '3': {'A': 'x:high', 'B': 'y:high'}}), encoding='utf-8')
        notes = self.dir / 'notas.tsv'
        notes.write_text('2\t3\t1\n', encoding='utf-8')
        with self.assertRaises(SystemExit):
            lg.cmd_unblind(SimpleNamespace(key=str(self.key), notes=str(notes)))


class Auth(unittest.TestCase):
    def status(self, payload):
        return mock.patch.object(lg.subprocess, 'run', return_value=SimpleNamespace(stdout=payload))

    def test_accepts_subscription(self):
        with self.status(json.dumps({'authMethod': 'claude.ai'})):
            lg.check_auth()

    def test_aborts_otherwise(self):
        for payload in (json.dumps({'authMethod': 'apiKey'}), 'não é json'):
            with self.status(payload), self.assertRaises(SystemExit):
                lg.check_auth()

    def test_api_key_is_removed_from_env(self):
        with mock.patch.dict(lg.os.environ, {'ANTHROPIC_API_KEY': 'x'}):
            self.assertNotIn('ANTHROPIC_API_KEY', lg.claude_env())


if __name__ == '__main__':
    unittest.main()
