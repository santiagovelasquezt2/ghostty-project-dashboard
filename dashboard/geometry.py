"""Remember native viewport proportions and internal tmux pane layouts."""
import time
from . import cli

ROLES=('left','terminal','notes','monitor')
def mask(state):
    return ','.join(role for role in ROLES if state.get('terminals',{}).get(role))

def measurements(session):
    sizes={}
    for role in ROLES:
        view=cli.option(session,'view_'+role)
        if not view: continue
        rows=cli.tmux('list-clients','-t',view,'-F','#{session_name}|#{client_width}|#{client_cell_width}',check=False)
        for row in rows.splitlines():
            fields=row.split('|')
            if len(fields)==3 and fields[0]==view:
                try:
                    columns,cell=map(int,fields[1:])
                    if columns>0 and cell>0: sizes[role]=(columns*cell,cell)
                except ValueError: pass
    return sizes

def capture(session,state):
    state=dict(state)
    layouts=dict(state.get('saved_geometry',{}))
    values=measurements(session)
    active=[r for r in ROLES if state.get('terminals',{}).get(r)]
    if all(r in values for r in active):
        layouts[mask(state)]={r:values[r][0] for r in active}
    state['saved_geometry']=layouts
    # Left/right horizontal divisions are independent of native column widths.
    internal={}
    for role in ('left','monitor'):
        pane=cli.option(session,'files' if role=='left' else 'monitor')
        if pane:
            internal[role]=cli.tmux('display-message','-p','-t',pane,'#{window_layout}',check=False)
    state['saved_internal']=internal
    return state

def restore(session,state):
    from . import native
    desired=state.get('saved_geometry',{}).get(mask(state))
    if desired:
        for attempt in range(10):
            time.sleep(0.12)
            current=measurements(session)
            if not all(r in current for r in desired): continue
            total=sum(current[r][0] for r in desired)
            old_total=sum(desired.values())
            moved=False
            # Outer borders first; notes controls its own boundary beside monitor.
            for role,direction in [('left','right'),('terminal','right'),('notes','right')]:
                if role not in desired: continue
                delta=round(desired[role]*total/old_total-current[role][0])
                if abs(delta)<current[role][1]: continue
                native.resize(state,role,direction if delta>0 else ('left' if direction=='right' else 'right'),min(abs(delta),4096))
                moved=True
                break
            if not moved: break
    for role,layout in state.get('saved_internal',{}).items():
        pane=cli.option(session,'files' if role=='left' else 'monitor')
        if pane and layout: cli.tmux('select-layout','-t',pane,layout,check=False)
