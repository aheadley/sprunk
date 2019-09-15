#!/usr/bin/env python3

import os.path
import sys
import random
import shlex
import urllib.parse

import click
import requests
import sprunk

class Radio:
    def __init__(self, defs, meta_url=None):
        self.defs = defs
        self.meta_url = meta_url
        self.padding = 0.5
        self.over_volume = 0.5
        self.random_lasts = {}

    def set_metadata(self, meta):
        parts = [self.defs.get('name'), meta.get('artist'), meta.get('title')]
        parts = [p for p in parts if p is not None]
        song = ' - '.join(parts)
        if not song:
            song = 'NO INFORMATION'

        print('###', song)

        if self.meta_url:
            parts = list(urllib.parse.urlparse(self.meta_url))
            query = dict(urllib.parse.parse_qsl(parts[4]))
            query['song'] = song
            parts[4] = urllib.parse.urlencode(query)
            our_meta_url = urllib.parse.urlunparse(parts)
            requests.get(our_meta_url)

    def choice(self, key):
        # FIXME better random
        if len(self.defs.get(key, [])) == 0:
            return None
        i = self.random_lasts.get(key, None)
        if i is None:
            i = 0
            random.shuffle(self.defs.get(key))
        if i >= len(self.defs.get(key)):
            i = 0
        self.random_lasts[key] = i + 1
        return self.defs.get(key)[i]

    def go_soft(self, soft_time, mainpath, overpath, meta, pre=0, post=None, force=False):
        if mainpath is None:
            return soft_time
        main = sprunk.FileSource(mainpath)
        if overpath:
            over = sprunk.FileSource(overpath)
        else:
            over = None

        # find the over start time, relative to music start time
        if over:
            over_start_time = pre - (over.size / over.samplerate + 2 * self.padding)
            skip_over = False
        else:
            over_start_time = 0
            skip_over = True

        # find out when the music starts
        if soft_time >= -over_start_time:
            # seamless music, nice
            main_start = soft_time
        elif force:
            # there must be a break to fit this in
            main_start = -over_start_time
        else:
            # we need a break but we can't force it
            main_start = soft_time
            skip_over = True
        over_start_time += main_start

        # ok, now we can do this
        md = self.music.add_source(main_start, main)
        self.music.add_callback(main_start, lambda _: self.set_metadata(meta))
        if post is None:
            post = md
        if skip_over:
            yield main_start + post
        else:
            self.music.set_volume(over_start_time, self.over_volume, duration=self.padding)
            od = self.talk.add_source(over_start_time + self.padding, over)
            yield over_start_time + self.padding + od
            self.music.set_volume(0, 1.0, duration=self.padding)
            yield main_start + post - (over_start_time + self.padding + od)
        return md - post

    def go_ad(self, sched, soft_time):
        return self.go_break(sched, soft_time, 'ad', 'to-ad', 'Advertisement')

    def go_news(self, sched, soft_time):
        return self.go_break(sched, soft_time, 'news', 'to-news', 'News')

    def go_break(self, sched, soft_time, main_set, over_set, title):
        ad = self.choice(main_set)
        p = self.choice(over_set)

        admeta = {
            'title': title,
        }

        if ad is not None:
            soft_time = yield from self.go_soft(soft_time, ad, p, admeta, force=True)
        return soft_time

    def go_id(self, sched, soft_time):
        idpath = self.choice('id')

        idmeta = {
            'title': 'Identification',
        }

        return self.go_soft(soft_time, idpath, None, idmeta)

    def go_solo(self, sched, soft_time):
        solo = self.choice('solo')

        solometa = {
            'title': 'Monologue',
        }

        return self.go_soft(soft_time, solo, None, solometa)

    def go_music(self, sched, soft_time):
        # select a song randomly
        m = self.choice('music')
        if random.random() < 0.3:
            p = self.choice('general')
        else:
            p = None

        return self.go_soft(soft_time, m['path'], p, m, pre=m['pre'], post=m['post'],)

    @sprunk.coroutine_method
    def go(self, sched):
        self.music = sched.subscheduler()
        self.talk = sched.subscheduler()

        soft_time = 0
        while True:
            for go_break in [self.go_ad, self.go_news]:
                for _ in range(4):
                    soft_time = yield from self.go_music(sched, soft_time)
                    yield self.padding
                soft_time = yield from go_break(sched, soft_time)
                yield self.padding
                soft_time = yield from self.go_id(sched, soft_time)
                yield self.padding
                soft_time = yield from self.go_solo(sched, soft_time)
                yield self.padding

def run(src, sink):
    src = src.reformat_like(sink)
    src.allocate(int(src.samplerate * 0.1))
    filled = src.buffer
    while len(filled) > 0:
        filled = src.fill()
        sink.write(filled)

def output_option(f):
    def open_sink(ctx, param, value):
        if value:
            types = ['file', 'stdout', 'ffmpeg', 'ffmpegre']
            typ = 'file'
            if value == '-':
                typ = 'stdout'
                value = ''
            if ':' in value:
                testtyp, testvalue = value.split(':', 1)
                if testtyp in types:
                    typ = testtyp
                    value = testvalue

            if typ == 'file':
                return sprunk.FileSink(value, 48000, 2)
            elif typ == 'stdout':
                # do some munging
                inputfile = sys.stdout.buffer
                sys.stdout = sys.stderr
                return sprunk.FileSink(inputfile, 48000, 2, format='RAW', subtype='PCM_16', endian='LITTLE')
            elif typ == 'ffmpeg':
                args = shlex.split(value)
                return sprunk.FFmpegSink(48000, 2, False, args)
            elif typ == 'ffmpegre':
                args = shlex.split(value)
                return sprunk.FFmpegSink(48000, 2, True, args)
            else:
                raise RuntimeError('unhandled output type')
        return sprunk.PyAudioSink(48000, 2)
    return click.option('-o', '--output', type=str, callback=open_sink)(f)

def input_argument(*args, **kwargs):
    def open_file(ctx, param, value):
        return sprunk.FileSource(value)
    return click.argument(*args, **kwargs, callback=open_file)

@click.group()
def cli():
    pass

@cli.command()
@output_option
@input_argument('SRC')
def play(output, src):
    run(src, output)

@sprunk.coroutine
def over_coroutine(sched, song, over):
    padding = 1
    start_over = 3
    oversched = sched.subscheduler()
    songsched = sched.subscheduler()

    songsched.add_source(0, song)
    over_length = oversched.add_source(start_over, over)
    yield start_over - padding
    full_volume = songsched.get_volume(0)
    songsched.set_volume(0, 0.5, duration=padding)
    yield padding + over_length
    songsched.set_volume(0, full_volume, duration=padding)

@cli.command()
@output_option
@input_argument('SONG')
@input_argument('OVER')
def over(output, song, over):
    sched = sprunk.Scheduler(output.samplerate, output.channels)
    over_coroutine(sched, song, over)
    run(sched, output)

@cli.command()
@click.argument('DEFINITIONS', nargs=-1)
@click.option('-e', '--extension', default='ogg')
def lint(definitions, extension):
    defs = sprunk.load_definitions(definitions, extension)
    return sprunk.definitions.lint(defs)

@cli.command()
@output_option
@click.argument('DEFINITIONS', nargs=-1)
@click.option('-e', '--extension', default='ogg')
@click.option('-m', '--meta-url')
def radio(output, definitions, extension, meta_url):
    defs = sprunk.load_definitions(definitions, extension)
    r = Radio(defs, meta_url)
    sched = sprunk.Scheduler(output.samplerate, output.channels)
    r.go(sched)
    run(sched, output)

if __name__ == '__main__':
    cli()
