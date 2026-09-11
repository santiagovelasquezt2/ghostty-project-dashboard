"""Notes/Commits occupy one persistent upper-left slot above File Changes."""
import shlex
import shutil
from . import cli, native, native_workspace as workspace


def ensure_native(session):
    session=workspace.controller(session)
    state=workspace.read_state(session)
    if not state: raise RuntimeError('Open dashboard before changing its top section.')
    # Retire the previous notes column through the existing ownership checks.
    if state.get('terminals',{}).get('notes'):
        workspace.toggle_notes(session)
        state=workspace.read_state(session)
    if state.get('terminals',{}).get('top'): return state
    with cli.locked(session+'-native'):
        state=workspace.read_state(session)
        if state.get('terminals',{}).get('top'): return state
        pane=cli.option(session,'notes' if cli.option(session,'top_mode')=='notes' else 'commits')
        files=cli.option(session,'files')
        window=cli.tmux('display-message','-p','-t',files,'#{window_id}')
        same=cli.tmux('display-message','-p','-t',pane,'#{window_id}')==window
        layout=cli.tmux('display-message','-p','-t',files,'#{window_layout}')
        if same: cli.tmux('break-pane','-d','-s',pane,'-n','dashboard-top')
        top_window=cli.tmux('display-message','-p','-t',pane,'#{window_id}')
        view=cli.option(session,'view_top')
        if not view or cli.tmux('display-message','-p','-t',view,'#{session_name}',check=False)!=view:
            view=cli.session_name(cli.option(session,'root'))+'-top'
            cli.tmux('new-session','-d','-t',session,'-s',view)
            cli.set_option(session,'view_top',view)
        cli.set_option(view,'parent',session);cli.set_option(view,'role','top')
        cli.tmux('select-window','-t',view+':'+top_window)
        cli.tmux('set-option','-t',view,'status','off')
        cli.tmux('set-option','-w','-t',pane,'pane-border-status','off')
        command=shlex.join(['/usr/bin/env','-u','TMUX','-u','TMUX_PANE','-u','NO_COLOR','COLORTERM=truecolor',shutil.which('tmux') or 'tmux','-L',cli.SOCKET,'attach-session','-t',view])
        try:
            top=native.add_top(state,command,cli.option(session,'root'))
        except Exception:
            if same:
                cli.tmux('join-pane','-d','-v','-b','-s',pane,'-t',files)
                cli.tmux('select-layout','-t',files,layout)
            raise
        updated={**state,'terminals':{**state['terminals'],'top':top}}
        workspace.write_state(session,updated)
        cli.set_option(session,'top',pane)
        cli.set_option(session,'top_mode',cli.option(session,'top_mode') or 'commits')
        cli.tmux('set-option','-w','-t',files,'pane-border-status','off')
        return updated


def toggle(session):
    session=workspace.controller(session)
    if cli.option(session,'backend')=='native': ensure_native(session)
    with cli.locked(session+'-top'):
        notes=workspace.prepare_notes(session)
        files=cli.option(session,'files')
        if cli.option(session,'top_mode') != 'notes' and cli.tmux('display-message','-p','-t',notes,'#{window_id}') == cli.tmux('display-message','-p','-t',files,'#{window_id}'):
            cli.tmux('break-pane','-d','-s',notes,'-n','dashboard-notes')
        is_notes=cli.option(session,'top_mode')=='notes'
        current=notes if is_notes else cli.option(session,'commits')
        replacement=cli.option(session,'commits') if is_notes else notes
        # The slot's dimensions stay identical, and both processes survive.
        cli.tmux('swap-pane','-d','-s',replacement,'-t',current)
        cli.set_option(session,'top',replacement)
        cli.set_option(session,'top_mode','commits' if is_notes else 'notes')
        cli.set_option(session,'notes_hidden','1' if is_notes else '0')
        cli.tmux('select-pane','-t',replacement)
