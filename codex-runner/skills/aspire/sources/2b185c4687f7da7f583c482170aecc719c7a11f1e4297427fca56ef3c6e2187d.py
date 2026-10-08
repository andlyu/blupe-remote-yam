import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scene_geometry import TaskBlocked, world_points, mask_points, fit_support, plane_z, rpy_from_rotation

QUERIES = {
 'green_top': 'Only the currently visible upward-facing flat top face of the solid green rectangular cuboid, including after stacking. Exclude side faces, green pen, towel and robot. Do not infer hidden pixels.',
 'red_top': 'Only the currently visible upward-facing top face of the sole center-left red rectangular cuboid, initially near pixel (230,265) in the 640x480 top image. Follow this cuboid after stacking. Exclude round red token, sides, green block and robot. Do not infer hidden pixels.',
 'red': 'All visible surfaces of the sole center-left red rectangular cuboid, initially near pixel (230,265) in the 640x480 top image, including exposed sides beneath green after stacking. Exclude the round red token and other objects.',
 'red_local_table': 'Exposed white tabletop immediately surrounding the sole center-left red rectangular cuboid initially near pixel (230,265), within roughly two block lengths. Exclude objects, towel, writing, robot, background and shadow boundaries.',
 'source_support': 'Exposed support immediately surrounding the base of the solid green rectangular cuboid: towel if resting on towel, otherwise tabletop. Exclude all objects and robot.'
}
SIGNS=np.array([[i,j,k] for i in (-1.,1.) for j in (-1.,1.) for k in (-1.,1.)])


def plain(v):
    if isinstance(v,np.ndarray): return v.tolist()
    if isinstance(v,np.generic): return v.item()
    if isinstance(v,dict): return {k:plain(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [plain(x) for x in v]
    return v


def unit(v):
    v=np.asarray(v,float)
    n=np.linalg.norm(v)
    if not np.isfinite(n) or n==0: raise TaskBlocked('Degenerate axis')
    return v/n


def observe(tools,names):
    f=tools['get_camera_rgbd']('top')
    c=np.asarray(f.get('metadata',{}).get('factory_color_intrinsics',{}).get('coeffs',[0.]*5))
    if c.shape!=(5,) or not np.isfinite(c).all() or np.any(c): raise TaskBlocked('Expected zero-distortion paired top capture')
    p=world_points(f)
    if np.asarray(f['rgb']).shape[:2]!=p.shape[:2]: raise TaskBlocked('Raster mismatch')
    proposals={}
    for name in names:
        proposal=tools['segment_camera_rgb'](f['rgb'],QUERIES[name])
        m=np.asarray(proposal['mask'])
        if m.dtype!=np.bool_ or m.shape!=p.shape[:2]: raise TaskBlocked('Malformed mask: '+name)
        proposals[name]=proposal
    return p,proposals,dict(camera='top',metadata=f.get('metadata',{}),K=f['K'],T_world_camera=f['T_world_camera'],proposals=proposals)


def cloud(p,m,name):
    return mask_points(p,m[name]['mask'])


def face(xyz):
    if len(xyz)<3: raise TaskBlocked('Insufficient top depth')
    plane=fit_support(xyz)
    n=unit([-plane[0],-plane[1],1.])
    xy=np.median(xyz[:,:2],axis=0)
    origin=np.r_[xy,plane_z(plane,xy)]
    _,axes=np.linalg.eigh(np.cov((xyz[:,:2]-xy).T))
    a=np.r_[axes[:,-1],0.]
    a=unit(a-n*np.dot(a,n))
    b=unit(np.cross(n,a))
    frame=np.column_stack([a,b,n])
    uv=(xyz-origin)@frame[:,:2]
    lo,hi=np.quantile(uv,[.01,.99],axis=0)
    center=origin+frame[:,:2]@((lo+hi)/2)
    size=hi-lo
    if size[1]>size[0]: frame,size=np.column_stack([b,-a,n]),size[::-1]
    if not np.isfinite(size).all() or np.any(size<=0): raise TaskBlocked('Invalid extent')
    return dict(top_center=center,dimensions=size,frame=frame,top_plane=plane,samples=len(xyz))


def supported(top,xyz):
    radius=2*float(np.linalg.norm(top['dimensions']))
    xyz=xyz[np.linalg.norm(xyz[:,:2]-top['top_center'][:2],axis=1)<=radius]
    support=fit_support(xyz)
    n,c=top['frame'][:,2],top['top_center']
    denominator=n[2]-np.dot(support[:2],n[:2])
    if denominator<=0: raise TaskBlocked('Invalid support relation')
    h=(c[2]-plane_z(support,c[:2]))/denominator
    if not np.isfinite(h) or h<=0: raise TaskBlocked('Invalid measured thickness')
    return dict(top,height_m=float(h),center=c-h*n/2),support,dict(plane=support,radius_m=radius,samples=len(xyz),residual_quantiles_m=np.quantile(xyz[:,2]-plane_z(support,xyz[:,:2]),[.05,.5,.95]))


def rectangle(c,b,s):
    return np.asarray(c)+(np.array([[-1.,-1.],[1.,-1.],[1.,1.],[-1.,1.]])*np.asarray(s)/2)@np.asarray(b).T


def overlap(poly,size):
    poly=np.asarray(poly)
    for axis in (0,1):
        for sign in (-1.,1.):
            if len(poly)==0: raise TaskBlocked('No contact overlap')
            out=[]
            prev=poly[-1]
            dp=size[axis]/2-sign*prev[axis]
            for cur in poly:
                dc=size[axis]/2-sign*cur[axis]
                if (dp>=0)!=(dc>=0): out.append(prev+(cur-prev)*dp/(dp-dc))
                if dc>=0: out.append(cur)
                prev,dp=cur,dc
            poly=np.asarray(out)
    if len(poly)<3: raise TaskBlocked('Degenerate contact overlap')
    return poly


def area(p):
    return abs(float(np.sum(p[:,0]*np.roll(p[:,1],-1)-p[:,1]*np.roll(p[:,0],-1))))/2


def inside(p,q):
    e=np.roll(p,-1,axis=0)-p
    v=q-p
    c=e[:,0]*v[:,1]-e[:,1]*v[:,0]
    return bool(np.all(c>=-1e-10) or np.all(c<=1e-10))


def boxes_for(station,side):
    data=station['planner_validation']['native_finger_boxes']
    if data['frame']!='aspire_grasp': raise TaskBlocked('Wrong native geometry frame')
    boxes=data['arms'][side]
    if not boxes: raise TaskBlocked('No native boxes')
    for b in boxes:
        for key,shape in [('half_sizes_m',(3,)),('rotation_grasp',(3,3)),('center_closed_grasp_m',(3,)),('center_open_grasp_m',(3,))]:
            a=np.asarray(b[key],float)
            if a.shape!=shape or not np.isfinite(a).all(): raise TaskBlocked('Malformed box')
    return boxes


def vertices(boxes,jaw):
    return np.concatenate([(1-jaw)*np.asarray(b['center_closed_grasp_m'])+jaw*np.asarray(b['center_open_grasp_m'])+(SIGNS*np.asarray(b['half_sizes_m']))@np.asarray(b['rotation_grasp']).T for b in boxes])


def acquisition(g,station,config):
    side,sign,tilt,skew,long_fraction,approach_kind=config
    jaw=station['gripper_geometry']
    if jaw['closing_axis'] not in ('grasp_x','grasp_y'): raise TaskBlocked('Unknown closing convention')
    closing=0 if jaw['closing_axis']=='grasp_x' else 1
    n=g['frame'][:,2]
    # Small in-plane obliquity changes wrist heading while still pinching
    # opposing long side walls across the measured short dimension.
    turn=Rotation.from_rotvec(n*np.deg2rad(skew)).as_matrix()
    axis=turn@(sign*g['frame'][:,1])
    z=-n
    base=np.column_stack([axis,np.cross(z,axis),z]) if closing==0 else np.column_stack([np.cross(axis,z),axis,z])
    rot=Rotation.from_rotvec(axis*np.deg2rad(tilt)).as_matrix()@base
    boxes=boxes_for(station,side)
    bodies=sorted(set(b['body'] for b in boxes))
    if len(bodies)!=2: raise TaskBlocked('Expected two finger bodies')
    desired=np.asarray(jaw['grip_pad_full_dimensions_m'])/2
    pads=[min([b for b in boxes if b['body']==body],key=lambda b:np.linalg.norm(np.asarray(b['half_sizes_m'])-desired)) for body in bodies]
    contacts=[]
    for pad in pads:
        half=np.asarray(pad['half_sizes_m'])
        index=int(np.argmax(half))
        direction=np.asarray(pad['rotation_grasp'])[:,index]
        if direction[2]<0: direction=-direction
        contacts.append(np.asarray(pad['center_closed_grasp_m'])+.9*half[index]*direction)
    local=np.mean(contacts,axis=0)
    desired_contact=g['top_center']-.15*g['height_m']*n+long_fraction*g['dimensions'][0]*g['frame'][:,0]
    site=desired_contact-rot@local
    relative=rot.T@(g['center']-site)
    body=rot.T@g['frame']
    # Side-wall intersections of the closing line quantify the oblique span;
    # neither measured motor aperture nor object retention is inferred here.
    a_body=g['frame'].T@axis
    t=g['dimensions'][1]/(2*abs(a_body[1]))
    endpoints=np.array([desired_contact-t*axis,desired_contact+t*axis])
    return site,rot,dict(side=side,parameters=config[1:],pad_contact_in_grasp=local,
        contact_world=desired_contact,pad_boxes=pads,oblique_contact_endpoints=endpoints,
        modeled_closing_span_m=2*t,payload_to_grasp_translation=relative,payload_to_grasp_rotation=body,
        modeled_finger_bottom_above_payload_bottom_m=float(np.min(((vertices(boxes,0.)-relative)@body)[:,2])+g['height_m']/2),
        provenance='Native exported geometry, unmeasured physical pads; no additional TCP or frame offset')


def placement(g,r,site,pickup,yaw):
    frame=r['frame']@Rotation.from_euler('z',yaw,degrees=True).as_matrix()
    delta=frame@g['frame'].T
    final=delta@pickup
    bottom=r['top_center']+.001*r['frame'][:,2]
    center=bottom+g['height_m']*frame[:,2]/2
    place=center-delta@(g['center']-site)
    footprint=(rectangle(bottom,frame[:,:2],g['dimensions'])-r['top_center'])@r['frame'][:,:2]
    poly=overlap(footprint,r['dimensions'])
    n=r['frame'][:,2]
    gravity=center-np.array([0.,0.,np.dot(n,center-r['top_center'])/n[2]])
    uv=(gravity-r['top_center'])@r['frame'][:,:2]
    return place,final,dict(center=center,frame=frame,footprint=footprint,overlap=poly,
        supported_area_fraction=area(poly)/area(footprint),gravity_projection=uv,gravity_over_contact=inside(poly,uv),
        overhang_m=np.maximum(np.max(np.abs(footprint),axis=0)-r['dimensions']/2,0.))


def move(stage,side,p,R):
    return dict(stage=stage,kind='move',arguments={side+'_target_pos':np.asarray(p).tolist(),side+'_target_rpy':rpy_from_rotation(R)})


def segments(steps,state):
    jaws={}
    for s in ('left','right'):
        value=np.asarray(state['arms'][s]['gripper_pos'],float).reshape(-1)
        if value.size!=1 or not np.isfinite(value[0]): raise TaskBlocked('Invalid jaws')
        jaws[s]=float(value[0])
    out=[]
    for step in steps:
        if step['kind'] in ('open','close'): jaws[step['side']]=float(step['kind']=='open')
        else: out.append(dict(stage=step['stage'],arguments=step['arguments'],gripper_state=dict(jaws)))
    return out


def candidate(g,r,station,state,config,yaw,carry_tilt,clearance):
    side=config[0]
    grasp,pickup,acq=acquisition(g,station,config)
    relative=acq['payload_to_grasp_translation']
    place,final,placed=placement(g,r,grasp,pickup,yaw)
    start=np.asarray(state['arms'][side]['ee_pos'])
    start_R=Rotation.from_quat(state['arms'][side]['ee_quat']).as_matrix()
    approach=grasp-.04*pickup[:,2] if config[-1]=='axis' else grasp+np.array([0.,0.,.04])
    low=grasp+[0.,0.,.035]
    inward=unit(np.r_[start[:2]-grasp[:2],0.])
    retracted=low+.06*inward
    radius=np.linalg.norm(np.r_[g['dimensions'],g['height_m']])/2
    z=max(g['top_center'][2],r['top_center'][2])+radius+clearance
    source_center=retracted+pickup@relative
    source_center[2]=max(z,source_center[2])
    target_center=np.r_[placed['center'][:2],source_center[2]]
    raised=source_center-pickup@relative
    closing=0 if station['gripper_geometry']['closing_axis']=='grasp_x' else 1
    carry=Rotation.from_rotvec(final[:,closing]*np.deg2rad(carry_tilt)).as_matrix()@final
    interp=Slerp([0.,1.],Rotation.from_matrix(np.stack([pickup,carry])))
    transfers=[]
    for t in (.25,.5,.75,1.):
        R=interp(t).as_matrix()
        center=(1-t)*source_center+t*target_center
        transfers.append((center-R@relative,R))
    boxes=boxes_for(station,side)
    verts=vertices(boxes,1.)@final.T
    block_corners=placed['center']+(SIGNS*np.r_[g['dimensions'],g['height_m']]/2)@placed['frame'].T
    withdrawal=max(.06,float(np.max(block_corners[:,2])-place[2]-np.min(verts[:,2])+.025))
    clear=place+[0.,0.,withdrawal]
    above=target_center-final@relative
    above[2]=max(above[2],clear[2])
    steps=[dict(stage='open_before_pickup',kind='open',side=side),move('source_approach',side,approach,pickup),
        move('source_grasp',side,grasp,pickup),dict(stage='close_on_green',kind='close',side=side),
        move('short_vertical_lift',side,low,pickup),move('retract_before_raise',side,retracted,pickup),move('raise_payload',side,raised,pickup)]
    steps += [move('direct_transfer_'+str(i+1),side,p,R) for i,(p,R) in enumerate(transfers)]
    steps += [move('align_supported_face',side,above,final),move('place_on_red',side,place,final),
        dict(stage='release_green',kind='open',side=side),move('open_geometry_at_release',side,place,final),
        move('withdraw_vertically',side,clear,final),move('retreat_above_target',side,above,final)]
    # The return remains freshly planned with open jaws. Keep the first
    # reverse point at least as high as the completed withdrawal.
    reverse=list(reversed(transfers))
    first_p,first_R=reverse[0]
    first_p=first_p.copy()
    first_p[2]=max(first_p[2],clear[2])
    reverse[0]=(first_p,first_R)
    steps += [move('reverse_transfer_'+str(i+1),side,p,R) for i,(p,R) in enumerate(reverse)]
    steps += [move('clear_target_view',side,raised,pickup),move('return_measured_start',side,start,start_R)]
    diagnosis=[]
    for jaw in (0.,.5,1.):
        v=place+vertices(boxes,jaw)@final.T
        diagnosis.append(dict(aperture=jaw,minimum_world_z_m=np.min(v[:,2]),minimum_height_above_red_plane_m=np.min(v[:,2]-plane_z(r['top_plane'],v[:,:2]))))
    return steps,dict(strategy='direct_oblique_short_span',side=side,source_configuration=config,
        acquisition=acq,yaw_deg=yaw,carry_tilt_deg=carry_tilt,clearance_m=clearance,
        grasp=grasp,pickup_rotation=pickup,place=place,final_rotation=final,placement=placed,
        withdrawal_pose=clear,native_finger_support_diagnosis=diagnosis)


def failed(feedback):
    return feedback.get('failing_stage') or next((s.get('stage') for s in feedback.get('stages',[]) if s.get('status')!='Success'),None)


def build_task(tools):
    station=tools['get_station_info']()
    state=tools['get_planning_state']()
    p,m,obs=observe(tools,list(QUERIES))
    g,source,se=supported(face(cloud(p,m,'green_top')),cloud(p,m,'source_support'))
    r,table,te=supported(face(cloud(p,m,'red_top')),cloud(p,m,'red_local_table'))
    sides=sorted(('left','right'),key=lambda s:np.linalg.norm(np.asarray(state['arms'][s]['ee_pos'])-g['top_center']))
    # Latest closest candidate saturates right joint 6 at -2.0944 rad.
    # Explore small heading changes rather than repeating its exact heading
    # with only transport variations. Contact remains on the measured sides.
    configs=[(s,-1.,tilt,skew,.22,'axis') for skew in (-8.,8.,-16.,16.) for tilt in (-30.,-40.,-20.) for s in sides]
    configs += [(s,-1.,tilt,skew,.32,'vertical') for skew in (-8.,8.,-16.,16.) for tilt in (-30.,-20.) for s in sides]
    configs += [(s,1.,30.,skew,.32,'vertical') for skew in (-8.,8.) for s in sides]
    trials=[]
    best=None
    best_progress=-1
    viable=[]
    def attempt(config,yaw,carry,height):
        nonlocal best,best_progress
        steps,geometry=candidate(g,r,station,state,config,yaw,carry,height)
        feedback=tools['plan_freespace_sequence'](segments(steps,state))
        progress=sum(s.get('status')=='Success' for s in feedback.get('stages',[]))
        trials.append(dict(configuration=geometry,complete_task=True,native_feedback=feedback))
        if best is None or progress>best_progress or feedback.get('success') is True:
            best=(steps,geometry,feedback)
            best_progress=progress
        return feedback,progress
    found=False
    for config in configs:
        feedback,progress=attempt(config,0.,0.,.04)
        if feedback.get('success') is True:
            found=True
            break
        if failed(feedback) not in ('source_approach','source_grasp','short_vertical_lift','retract_before_raise'):
            viable.append((progress,config))
            # Spend a few complete plans on a feasible source immediately.
            for yaw,carry in ((180.,0.),(90.,0.),(-90.,0.)):
                feedback,progress=attempt(config,yaw,carry,.04)
                if feedback.get('success') is True:
                    found=True
                    break
        if found or len(trials)>=72: break
    if not found:
        options=[(yaw,carry,height) for height in (.04,.07) for carry in (30.,-30.,0.) for yaw in (0.,180.,90.,-90.,45.,-45.)]
        for yaw,carry,height in options:
            for _,config in sorted(viable,key=lambda x:-x[0])[:4]:
                if len(trials)>=96: break
                feedback,_=attempt(config,yaw,carry,height)
                if feedback.get('success') is True:
                    found=True
                    break
            if found or len(trials)>=96 or not viable: break
    if best is None: raise TaskBlocked('No complete proposal')
    steps,geometry,feedback=best
    return plain(dict(steps=steps,station=station,planning_start=state,initial_observation=obs,
        geometry=dict(green=g,red=r,source_support=source,table_plane=table,**geometry),
        local_support_evidence=dict(source=se,red=te),planning_trials=trials,
        candidate_preview_success=feedback.get('success') is True,
        target_identity='Sole current center-left red cuboid',
        previous_review=dict(latest='PLAN_FAILED at source_grasp; zero physical motion. Best native sample had right joint 6 at -2.0944 rad, 6.75 mm position and 2.21 degree rotation residuals.',
            v5_operator='It fell off the red... while the arm was moving back.',physical_review='FAILED',original_native_receipt='UNVERIFIED'),
        reasoning=['Revise source heading near the closest native solution using small oblique short-span pinches and measured side-wall contact. Wrist saturation is evidence, not a proven sole cause.',
            'Retain actual native pad offsets and supporting faces. No guessed world pose, TCP correction, relaxed collision check or elevated release workaround.',
            'Native complete plans include release with open geometry, withdrawal and measured-start return. Recorded plans are not live cache authorization.',
            'Runtime owns admission, fresh full planning, physical execution, recording through parking and shutdown. No physical retry loop.',
            'Fresh final evidence is required; planning does not prove retention or stable stacking.']))


def evaluate(tools,task):
    e=dict(outcome='UNVERIFIED',operator_observation=None,limitations='Post-retreat contact is inferred from depth. Runner must reassess after parking; ambiguous support remains UNVERIFIED.')
    try:
        p,m,obs=observe(tools,['green_top','red','red_top'])
        e['observation']=obs
        old=task['geometry']
        red=old['red']
        rc,rf,rs=np.asarray(red['top_center']),np.asarray(red['frame']),np.asarray(red['dimensions'])
        g=face(cloud(p,m,'green_top'))
        rp,rt=cloud(p,m,'red'),cloud(p,m,'red_top')
        if len(rp)<3: raise TaskBlocked('Insufficient fresh red depth')
        uv=(rp-rc)@rf[:,:2]
        at_target=bool(np.mean(np.all(np.abs(uv)<=rs/2+.008,axis=1))>.8)
        method='fresh visible red top'
        if len(rt)>=3: support=fit_support(rt)
        else:
            support=np.asarray(red['top_plane']).copy()
            edge=float(np.quantile(rp[:,2]-plane_z(support,rp[:,:2]),.95))
            boundary=np.min(np.abs(np.abs(uv)-rs/2),axis=1)
            corroborated=bool(at_target and abs(edge)<=.006 and np.ptp(rp[:,2])>=.35*red['height_m'] and np.mean(boundary<=.008)>.6 and np.max(np.ptp(uv,axis=0))>=.5*min(rs))
            e['red_edge_evidence']=dict(edge_residual_m=edge,corroborated=corroborated)
            if not corroborated: raise TaskBlocked('Hidden support lacks fresh side/edge corroboration')
            support[2]+=edge
            method='Historical slope corroborated by fresh side and upper edge'
        n=g['frame'][:,2]
        height=old['green']['height_m']
        bottom=g['top_center']-height*n
        footprint=(rectangle(bottom,g['frame'][:,:2],g['dimensions'])-rc)@rf[:,:2]
        poly=overlap(footprint,rs)
        contact=rc+poly@rf[:,:2].T
        bp=np.asarray(g['top_plane']).copy()
        bp[2]-=height/n[2]
        gaps=plane_z(bp,contact[:,:2])-plane_z(support,contact[:,:2])
        offset=(bottom-rc)@rf[:,:2]
        center=g['top_center']-height*n/2
        projection=np.r_[center[:2],plane_z(support,center[:2])]
        gravity=(projection-rc)@rf[:,:2]
        checks=dict(centered=bool(np.all(np.abs(offset)<rs*.25)),
            parallel=bool(np.dot(n,unit([-support[0],-support[1],1.]))>np.cos(np.deg2rad(8))),
            original_face=bool(np.allclose(np.sort(g['dimensions']),np.sort(old['green']['dimensions']),rtol=.20,atol=.005)),
            red_at_target=at_target,red_height_consistent=bool(abs(plane_z(support,rc[:2])-rc[2])<=.008),
            finite_gravity_support=inside(poly,gravity),contact_consistent=bool(abs(float(np.min(gaps)))<=.006 and np.max(gaps)<=.010))
        success=bool(all(checks.values()))
        e.update(outcome='PHYSICAL_SUCCESS' if success else 'UNVERIFIED',checks=checks,green_final=g,
            support_plane=support,support_method=method,finite_overlap=poly,center_offset_m=offset,
            supported_area_fraction=area(poly)/area(footprint),gap_range_m=[np.min(gaps),np.max(gaps)])
        return dict(success=success,evidence=plain(e))
    except (TaskBlocked,ValueError,KeyError,np.linalg.LinAlgError) as exc:
        e['reason']=type(exc).__name__+': '+str(exc)
        return dict(success=False,evidence=plain(e))
