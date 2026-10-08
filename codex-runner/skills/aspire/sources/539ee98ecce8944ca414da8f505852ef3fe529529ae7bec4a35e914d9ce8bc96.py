import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scene_geometry import TaskBlocked, world_points, mask_points, fit_support, plane_z, rpy_from_rotation

QUERIES = {
 'green_top': 'Only the currently visible upward-facing flat top face of the solid thick green rectangular cuboid, including after stacking. Exclude side faces, thin green rod, towel and robot. Do not infer hidden pixels.',
 'red_top': 'Only the visible upward-facing top face of the sole center-left red cuboid, initially near pixel (230,265) in the top image. Follow this cuboid after stacking. Exclude sides, round red token, green block and robot. Do not infer hidden pixels.',
 'red': 'All visible surfaces of the sole center-left red cuboid, initially near pixel (230,265), including exposed sides beneath green after stacking. Exclude round tokens and other objects.',
 'source_support': 'Exposed towel immediately surrounding the base of the thick green cuboid. Exclude both green objects, robot and tabletop.',
 'red_local_table': 'Exposed white tabletop immediately surrounding the sole center-left red cuboid within two block lengths. Exclude objects, towel, writing, robot and shadow boundaries.'
}


def plain(x):
    if isinstance(x,np.ndarray): return x.tolist()
    if isinstance(x,np.generic): return x.item()
    if isinstance(x,dict): return {k:plain(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)): return [plain(v) for v in x]
    return x


def unit(x):
    x=np.asarray(x,float)
    n=np.linalg.norm(x)
    if not np.isfinite(n) or n==0: raise TaskBlocked('Degenerate axis')
    return x/n


def interp(a,b,t):
    return Slerp([0.,1.],Rotation.from_matrix(np.stack([a,b])))(t).as_matrix()


def observe(tools,names):
    f=tools['get_camera_rgbd']('top')
    coeff=np.asarray(f.get('metadata',{}).get('factory_color_intrinsics',{}).get('coeffs',[0.]*5))
    if coeff.shape!=(5,) or not np.isfinite(coeff).all() or np.any(coeff): raise TaskBlocked('Expected zero-distortion top capture')
    p=world_points(f)
    if np.asarray(f['rgb']).shape[:2]!=p.shape[:2]: raise TaskBlocked('Paired raster mismatch')
    m={}
    for name in names:
        q=tools['segment_camera_rgb'](f['rgb'],QUERIES[name])
        a=np.asarray(q['mask'])
        if a.dtype!=np.bool_ or a.shape!=p.shape[:2]: raise TaskBlocked('Malformed mask: '+name)
        m[name]=q
    return p,m,dict(metadata=f.get('metadata',{}),K=f['K'],T_world_camera=f['T_world_camera'],proposals=m)


def cloud(p,m,name):
    return mask_points(p,m[name]['mask'])


def face(x):
    if len(x)<3: raise TaskBlocked('Insufficient face depth')
    plane=fit_support(x)
    n=unit([-plane[0],-plane[1],1.])
    xy=np.median(x[:,:2],axis=0)
    origin=np.r_[xy,plane_z(plane,xy)]
    _,axes=np.linalg.eigh(np.cov((x[:,:2]-xy).T))
    a=np.r_[axes[:,-1],0.]
    a=unit(a-n*(a@n))
    b=unit(np.cross(n,a))
    frame=np.column_stack([a,b,n])
    lo,hi=np.quantile((x-origin)@frame[:,:2],[.01,.99],axis=0)
    center=origin+frame[:,:2]@((lo+hi)/2)
    size=hi-lo
    if size[1]>size[0]: frame,size=np.column_stack([b,-a,n]),size[::-1]
    if np.any(size<=0) or not np.isfinite(size).all(): raise TaskBlocked('Invalid face extent')
    return dict(top_center=center,frame=frame,dimensions=size,top_plane=plane,samples=len(x))


def supported(top,x):
    radius=2*np.linalg.norm(top['dimensions'])
    x=x[np.linalg.norm(x[:,:2]-top['top_center'][:2],axis=1)<=radius]
    plane=fit_support(x)
    c,n=top['top_center'],top['frame'][:,2]
    den=n[2]-plane[:2]@n[:2]
    if den<=0: raise TaskBlocked('Invalid support geometry')
    h=(c[2]-plane_z(plane,c[:2]))/den
    if h<=0 or not np.isfinite(h): raise TaskBlocked('Invalid thickness')
    return dict(top,height_m=h,center=c-h*n/2),dict(plane=plane,samples=len(x),radius_m=radius)


def rectangle(c,f,s):
    return c+(np.array([[-1.,-1.],[1.,-1.],[1.,1.],[-1.,1.]])*np.asarray(s)/2)@f[:,:2].T


def overlap(p,size):
    p=np.asarray(p)
    for axis in (0,1):
        for sign in (-1.,1.):
            if len(p)==0: raise TaskBlocked('No finite overlap')
            out,prev=[],p[-1]
            dp=size[axis]/2-sign*prev[axis]
            for cur in p:
                dc=size[axis]/2-sign*cur[axis]
                if (dp>=0)!=(dc>=0): out.append(prev+(cur-prev)*dp/(dp-dc))
                if dc>=0: out.append(cur)
                prev,dp=cur,dc
            p=np.asarray(out)
    if len(p)<3: raise TaskBlocked('Degenerate overlap')
    return p


def area(p):
    return abs(float(np.sum(p[:,0]*np.roll(p[:,1],-1)-p[:,1]*np.roll(p[:,0],-1))))/2


def inside(p,q):
    e,v=np.roll(p,-1,axis=0)-p,q-p
    c=e[:,0]*v[:,1]-e[:,1]*v[:,0]
    return bool(np.all(c>=-1e-10) or np.all(c<=1e-10))


def signs3():
    return np.array([[i,j,k] for i in (-1.,1.) for j in (-1.,1.) for k in (-1.,1.)])


def fingers(station,side,jaw):
    data=station['planner_validation']['native_finger_boxes']
    if data['frame']!='aspire_grasp': raise TaskBlocked('Unexpected finger frame')
    boxes=data['arms'][side]
    if not boxes: raise TaskBlocked('Missing boxes')
    return np.concatenate([(1-jaw)*np.asarray(b['center_closed_grasp_m'])+jaw*np.asarray(b['center_open_grasp_m'])+(signs3()*np.asarray(b['half_sizes_m']))@np.asarray(b['rotation_grasp']).T for b in boxes])


def move(name,side,p,R):
    return dict(stage=name,kind='move',arguments={side+'_target_pos':np.asarray(p).tolist(),side+'_target_rpy':rpy_from_rotation(R)})


def segments(steps,state):
    jaws={s:float(np.asarray(state['arms'][s]['gripper_pos']).reshape(-1)[0]) for s in ('left','right')}
    out=[]
    for st in steps:
        if st['kind'] in ('open','close'): jaws[st['side']]=float(st['kind']=='open')
        else: out.append(dict(stage=st['stage'],arguments=st['arguments'],gripper_state=dict(jaws)))
    return out


def acquire(g,station,config):
    side,sign,tilt,skew,approach_kind=config
    n=g['frame'][:,2]
    closing=Rotation.from_rotvec(n*np.deg2rad(skew)).as_matrix()@(sign*g['frame'][:,1])
    z=-n
    axis=station['gripper_geometry']['closing_axis']
    if axis=='grasp_x': base,index=np.column_stack([closing,np.cross(z,closing),z]),0
    elif axis=='grasp_y': base,index=np.column_stack([np.cross(closing,z),closing,z]),1
    else: raise TaskBlocked('Unknown closing convention')
    R=Rotation.from_rotvec(closing*np.deg2rad(tilt)).as_matrix()@base
    p=g['top_center'].copy()
    p[2]-=.25*g['height_m']
    span=np.sum(np.abs(R[:,index]@g['frame'])*np.r_[g['dimensions'],g['height_m']])
    if span>=.0752: raise TaskBlocked('Measured span exceeds native opening')
    return p,R,dict(payload_to_grasp_translation=R.T@(g['center']-p),payload_to_grasp_rotation=R.T@g['frame'],projected_closing_span_m=span,nominal_pad_correction_applied=False)


def candidate(g,r,station,state,config,yaw,mode):
    side=config[0]
    idle='left' if side=='right' else 'right'
    grasp,pickup,acq=acquire(g,station,config)
    rel=acq['payload_to_grasp_translation']
    body=r['frame']@Rotation.from_euler('z',yaw,degrees=True).as_matrix()
    delta=body@g['frame'].T
    final=delta@pickup
    bottom=r['top_center']+.001*r['frame'][:,2]
    target_center=bottom+g['height_m']*body[:,2]/2
    place=target_center-delta@(g['center']-grasp)
    footprint=(rectangle(bottom,body,g['dimensions'])-r['top_center'])@r['frame'][:,:2]
    poly=overlap(footprint,r['dimensions'])
    gravity=(np.r_[target_center[:2],plane_z(r['top_plane'],target_center[:2])]-r['top_center'])@r['frame'][:,:2]
    start=np.asarray(state['arms'][side]['ee_pos'])
    start_R=Rotation.from_quat(state['arms'][side]['ee_quat']).as_matrix()
    idle_start=np.asarray(state['arms'][idle]['ee_pos'])
    idle_R=Rotation.from_quat(state['arms'][idle]['ee_quat']).as_matrix()
    park=idle_start+[0.,.08 if idle=='left' else -.08,.04]
    approach=grasp-.07*pickup[:,2] if config[-1]=='axis' else grasp+[0.,0.,.055]
    middle=(start+approach)/2
    middle[2]=max(start[2],approach[2])
    low=grasp+[0.,0.,.035]
    retract=low+.06*unit(np.r_[start[:2]-grasp[:2],0.])
    radius=np.linalg.norm(np.r_[g['dimensions'],g['height_m']])/2
    center=retract+pickup@rel
    center[2]=max(center[2],max(g['top_center'][2],r['top_center'][2])+radius+.05)
    raised=center-pickup@rel
    prefix=[dict(stage='open_before_pickup',kind='open',side=side),move('source_orientation_midpoint',side,middle,interp(start_R,pickup,.5)),move('source_approach',side,approach,pickup)]
    pregrasp=grasp-.02*pickup[:,2] if config[-1]=='axis' else grasp+[0.,0.,.02]
    prefix += [move('source_pregrasp',side,pregrasp,pickup),move('source_grasp',side,grasp,pickup),dict(stage='close_on_green',kind='close',side=side),
        move('short_vertical_lift',side,low,pickup),move('retract_before_raise',side,retract,pickup),move('raise_payload',side,raised,pickup),
        dict(stage='open_idle_arm',kind='open',side=idle),move('park_idle_arm_clear',idle,park,idle_R)]
    corners=target_center+(signs3()*np.r_[g['dimensions'],g['height_m']]/2)@body.T
    top_z=float(np.max(corners[:,2]))
    fv=fingers(station,side,1.)
    lift=max(.07,top_z-place[2]-np.min((fv@final.T)[:,2])+.025)
    clear=place+[0.,0.,lift]
    above=np.r_[place[:2],max(clear[2],center[2]-(final@rel)[2])]
    high_center=above+final@rel
    steps=list(prefix)
    way=[]
    def payload(name,c,R):
        p=c-R@rel
        steps.append(move(name,side,p,R))
        way.append((p.copy(),R.copy()))
    if mode=='native_direct': payload('connect_terminal',high_center,final)
    elif mode=='sequential':
        for i,t in enumerate((.33,.67,1.)): payload('early_rotation_'+str(i),center,interp(pickup,final,t))
        for i,t in enumerate((.33,.67,1.)): payload('translation_'+str(i),(1-t)*center+t*high_center,final)
    else:
        for i,t in enumerate((.25,.5,.75,1.)): payload('transfer_'+str(i),(1-t)*center+t*high_center,interp(pickup,final,t))
    steps += [move('place_on_red',side,place,final),dict(stage='release_green',kind='open',side=side),move('open_geometry_at_release',side,place,final)]
    for i,t in enumerate((.25,.5,.75,1.)): steps.append(move('withdraw_vertical_'+str(i),side,place+[0.,0.,lift*t],final))
    steps.append(move('retreat_above_target',side,above,final))
    for i,(p,R) in enumerate(reversed(way)):
        p=p.copy()
        p[2]=max(p[2],top_z-np.min((fv@R.T)[:,2])+.025)
        steps.append(move('reverse_transfer_'+str(i),side,p,R))
    mid=(raised+start)/2
    mid[2]=max(raised[2],start[2])
    steps += [move('clear_target_view',side,raised,pickup),move('return_orientation_midpoint',side,mid,interp(pickup,start_R,.5)),
        move('return_active_measured_start',side,start,start_R),move('return_idle_measured_start',idle,idle_start,idle_R)]
    probe=[dict(stage='probe_open_idle',kind='open',side=idle),move('probe_park_idle',idle,park,idle_R),dict(stage='probe_close_active',kind='close',side=side),move('probe_supported_terminal',side,place,final)]
    geo=dict(strategy='source_branch_search_then_terminal_connection',side=side,configuration=config,yaw_deg=yaw,connection_mode=mode,grasp=grasp,pickup_rotation=pickup,acquisition=acq,place=place,final_rotation=final,
        placement=dict(center=target_center,frame=body,footprint=footprint,overlap=poly,gravity_over_contact=inside(poly,gravity),supported_area_fraction=area(poly)/area(footprint),overhang_m=np.maximum(np.max(np.abs(footprint),axis=0)-r['dimensions']/2,0.)),withdrawal_pose=clear)
    return steps,geo,prefix,probe


def failure(f):
    return f.get('failing_stage') or next((s.get('stage') for s in f.get('stages',[]) if s.get('status')!='Success'),'')


def build_task(tools):
    station=tools['get_station_info']()
    state=tools['get_planning_state']()
    p,m,obs=observe(tools,list(QUERIES))
    g,se=supported(face(cloud(p,m,'green_top')),cloud(p,m,'source_support'))
    r,te=supported(face(cloud(p,m,'red_top')),cloud(p,m,'red_local_table'))
    # Prior -28 degree skew changed solver branch and regressed at approach.
    # Search tilt and approach order as well as heading; retain site-centered
    # contact and the measured face, rather than copying old world rotations.
    configurations=[('right',-1.,t,s,a) for t,s,a in [(-60.,-12.,'axis'),(-65.,-12.,'axis'),(-50.,-8.,'axis'),(-55.,-8.,'vertical'),(-55.,-16.,'vertical'),(-45.,0.,'axis'),(-65.,0.,'axis'),(-40.,8.,'vertical')]]
    configurations += [('right',1.,t,s,a) for t,s,a in [(30.,-8.,'vertical'),(45.,0.,'axis'),(60.,-8.,'vertical'),(15.,8.,'axis')]]
    configurations += [('left',sign,t,s,a) for sign,t,s,a in [(-1.,-55.,-12.,'vertical'),(1.,30.,-8.,'vertical'),(-1.,-35.,8.,'axis'),(1.,50.,0.,'axis')]]
    trials=[]
    best=None
    best_score=(-1,-1)
    source_successes=0
    solved=False
    def preview(steps,geo,scope):
        f=tools['plan_freespace_sequence'](segments(steps,state))
        bad=next((v for v in f.get('stages',[]) if v.get('status')!='Success'),{})
        classification='SUCCESS' if f.get('success') is True else ('IK_FAILURE' if bad.get('status')=='IK_Failed' else 'PATH_OR_COLLISION_FAILURE')
        trials.append(dict(scope=scope,configuration=geo,complete_task=scope=='complete_task',native_feedback=f,classification=classification))
        return f
    for config in configurations:
        steps,geo,prefix,probe=candidate(g,r,station,state,config,0.,'native_direct')
        if best is None: best=(steps,geo,False)
        sf=preview(prefix,geo,'source_prefix')
        done=sum(v.get('status')=='Success' for v in sf.get('stages',[]))
        if (0,done)>best_score: best,best_score=(steps,geo,False),(0,done)
        if sf.get('success') is not True: continue
        source_successes+=1
        # Supported terminal feasibility precedes every carry sweep.
        feasible=[]
        for yaw in (0.,30.,60.,90.,120.,150.,180.,-150.,-120.,-90.,-60.,-30.):
            steps,geo,_,probe=candidate(g,r,station,state,config,yaw,'native_direct')
            tf=preview(probe,geo,'supported_terminal_probe')
            if tf.get('success') is True: feasible.append(yaw)
            if len(feasible)>=3: break
        for yaw in feasible:
            for mode in ('native_direct','coupled','sequential'):
                steps,geo,_,_=candidate(g,r,station,state,config,yaw,mode)
                f=preview(steps,geo,'complete_task')
                score=(int(f.get('success') is True),sum(v.get('status')=='Success' for v in f.get('stages',[])))
                if score>best_score: best,best_score=(steps,geo,f.get('success') is True),score
                if f.get('success') is True:
                    solved=True
                    break
                if failure(f).startswith('source_'): break
            if solved: break
        if solved or source_successes>=4: break
    if best is None: raise TaskBlocked('No complete proposal')
    steps,geo,passed=best
    return plain(dict(steps=steps,geometry=dict(green=g,red=r,source_support=se['plane'],table_plane=te['plane'],**geo),station=station,planning_start=state,initial_observation=obs,
        local_support_evidence=dict(source=se,red=te),planning_trials=trials,candidate_preview_success=passed,target_identity='Sole current center-left red cuboid',
        previous_review=dict(latest='No physical motion. Right/-55/-28 approach failed with 15.58 mm and 47.15 degree native residuals, joint 5 at its upper bound.',
            perception='Identical RGB-D capture produced different Astra polygons and fitted normals; this is not physical object motion.',v11_physical='FAILED pickup despite completed packets.',v5_operator='It fell off the red... while the arm was moving back.',v5_native='UNVERIFIED'),
        reasoning=['Recheck source feasibility using measured site-centered short-span grasps with tilt and approach-order alternatives; do not presume the previous named grasp remains feasible.',
            'Log source, terminal and complete-task native feedback together with explicit scope so diagnostic failures remain visible.',
            'Probe actual supported terminal poses for source-feasible grasps before any carry sweep. Native path failure does not prove absolute goal unreachability.',
            'Final pose preserves the measured original supporting face and rigid payload transform, with free yaw and finite overlap/overhang evidence.',
            'No nominal pad correction, guessed flat normal, arbitrary release elevation, native-check change, or physical retry loop.',
            'Every returned sequence includes release, open-jaw withdrawal and both returns. Runner owns fresh full planning, physical enablement, recording through parking and shutdown.']))


def evaluate(tools,task):
    e=dict(outcome='UNVERIFIED',operator_observation=None,limitations='Fresh depth-based support inference; runner must reassess after normal parking. Packets do not prove retention.')
    try:
        p,m,obs=observe(tools,['green_top','red','red_top'])
        e['observation']=obs
        old=task['geometry']
        r=old['red']
        rc,rf,size=np.asarray(r['top_center']),np.asarray(r['frame']),np.asarray(r['dimensions'])
        g=face(cloud(p,m,'green_top'))
        rp,rt=cloud(p,m,'red'),cloud(p,m,'red_top')
        if len(rp)<3: raise TaskBlocked('Insufficient fresh red evidence')
        uv=(rp-rc)@rf[:,:2]
        at_target=bool(np.mean(np.all(np.abs(uv)<=size/2+.008,axis=1))>.8)
        if len(rt)>=3: support,method=fit_support(rt),'Fresh visible red top'
        else:
            support=np.asarray(r['top_plane']).copy()
            edge=float(np.quantile(rp[:,2]-plane_z(support,rp[:,:2]),.95))
            boundary=np.min(np.abs(np.abs(uv)-size/2),axis=1)
            confirmed=bool(at_target and abs(edge)<=.006 and np.ptp(rp[:,2])>=.35*r['height_m'] and np.mean(boundary<=.008)>.6 and np.max(np.ptp(uv,axis=0))>=.5*min(size))
            e['red_edge_evidence']=dict(residual_m=edge,corroborated=confirmed)
            if not confirmed: raise TaskBlocked('Hidden support lacks fresh edge corroboration')
            support[2]+=edge
            method='Historical slope corroborated by fresh red edge'
        h,n=old['green']['height_m'],g['frame'][:,2]
        bottom=g['top_center']-h*n
        footprint=(rectangle(bottom,g['frame'],g['dimensions'])-rc)@rf[:,:2]
        poly=overlap(footprint,size)
        contact=rc+poly@rf[:,:2].T
        bp=g['top_plane'].copy()
        bp[2]-=h/n[2]
        gaps=plane_z(bp,contact[:,:2])-plane_z(support,contact[:,:2])
        center=g['top_center']-h*n/2
        gravity=(np.r_[center[:2],plane_z(support,center[:2])]-rc)@rf[:,:2]
        offset=(bottom-rc)@rf[:,:2]
        checks=dict(red_at_target=at_target,centered=bool(np.all(np.abs(offset)<size*.25)),original_face=bool(np.allclose(np.sort(g['dimensions']),np.sort(old['green']['dimensions']),rtol=.20,atol=.005)),
            parallel=bool(n@unit([-support[0],-support[1],1.])>np.cos(np.deg2rad(8))),red_height_consistent=bool(abs(plane_z(support,rc[:2])-rc[2])<=.008),
            finite_gravity_support=inside(poly,gravity),contact_consistent=bool(abs(float(np.min(gaps)))<=.006 and np.max(gaps)<=.010))
        success=bool(all(checks.values()))
        e.update(outcome='PHYSICAL_SUCCESS' if success else 'UNVERIFIED',checks=checks,green_final=g,support_plane=support,support_method=method,finite_overlap=poly,
            supported_area_fraction=area(poly)/area(footprint),center_offset_m=offset,gap_range_m=[np.min(gaps),np.max(gaps)])
        return dict(success=success,evidence=plain(e))
    except (TaskBlocked,ValueError,KeyError,np.linalg.LinAlgError) as exc:
        e['reason']=type(exc).__name__+': '+str(exc)
        return dict(success=False,evidence=plain(e))
