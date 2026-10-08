import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scene_geometry import TaskBlocked, world_points, mask_points, fit_support, plane_z, rpy_from_rotation

QUERIES = {
 'green_top': 'Only the upward-facing flat top surface of the solid green rectangular cuboid. Exclude side faces, green pen, towel and robot.',
 'red_top': 'Only the visible upward-facing top surface of the red rectangular cuboid. Exclude its sides, green block, round token and pink figure. Do not infer hidden pixels.',
 'red': 'All visible surfaces of the red rectangular cuboid, including exposed sides underneath green. Exclude round tokens and pink figure.',
 'red_local_table': 'Exposed white tabletop immediately around the red rectangular cuboid, on all visible sides within roughly two red-block lengths. Exclude every object, block, towel, shadow boundary, writing, robot and background. Do not include distant tabletop.',
 'source_support': 'Exposed supporting surface immediately surrounding the base of the green rectangular cuboid: towel if on towel, otherwise tabletop. Exclude all objects and robot.'
}


def plain(v):
    if isinstance(v,np.ndarray): return v.tolist()
    if isinstance(v,np.generic): return v.item()
    if isinstance(v,dict): return {k:plain(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [plain(x) for x in v]
    return v


def unit(v):
    v=np.asarray(v,dtype=float)
    n=np.linalg.norm(v)
    if not np.isfinite(n) or n==0: raise TaskBlocked('Degenerate axis')
    return v/n


def observe(tools,names):
    f=tools['get_camera_rgbd']('top')
    c=np.asarray(f.get('metadata',{}).get('factory_color_intrinsics',{}).get('coeffs',[0.]*5))
    if c.shape!=(5,) or not np.isfinite(c).all() or np.any(c):
        raise TaskBlocked('Expected zero-distortion top capture')
    p=world_points(f)
    if np.asarray(f['rgb']).shape[:2]!=p.shape[:2]: raise TaskBlocked('Paired raster mismatch')
    masks={}
    for name in names:
        proposal=tools['segment_camera_rgb'](f['rgb'],QUERIES[name])
        m=np.asarray(proposal['mask'])
        if m.dtype!=np.bool_ or m.shape!=p.shape[:2]: raise TaskBlocked('Malformed mask: '+name)
        masks[name]=proposal
    return p,masks,dict(camera='top',metadata=f.get('metadata',{}),K=f['K'],
        T_world_camera=f['T_world_camera'],proposals=masks)


def cloud(p,masks,name):
    return mask_points(p,masks[name]['mask'])


def face(xyz):
    if len(xyz)<3: raise TaskBlocked('Insufficient face depth')
    plane=fit_support(xyz)
    normal=unit([-plane[0],-plane[1],1.])
    xy=np.median(xyz[:,:2],axis=0)
    origin=np.r_[xy,plane_z(plane,xy)]
    _,axes=np.linalg.eigh(np.cov((xyz[:,:2]-xy).T))
    a=np.r_[axes[:,-1],0.]
    a=unit(a-normal*np.dot(a,normal))
    b=unit(np.cross(normal,a))
    frame=np.column_stack([a,b,normal])
    uv=(xyz-origin) @ frame[:,:2]
    lo,hi=np.quantile(uv,[.01,.99],axis=0)
    center=origin+frame[:,:2] @ ((lo+hi)/2)
    size=hi-lo
    if size[1]>size[0]: frame,size=np.column_stack([b,-a,normal]),size[::-1]
    if not np.isfinite(size).all() or np.any(size<=0): raise TaskBlocked('Invalid face dimensions')
    return dict(top_center=center,dimensions=size,frame=frame,top_plane=plane,samples=len(xyz))


def local_support(xyz,top):
    radius=2.*float(np.linalg.norm(top['dimensions']))
    xyz=xyz[np.linalg.norm(xyz[:,:2]-top['top_center'][:2],axis=1)<=radius]
    plane=fit_support(xyz)
    residual=xyz[:,2]-plane_z(plane,xyz[:,:2])
    return plane,dict(plane=plane,local_samples=len(xyz),radius_m=radius,
        xyz_bounds=[np.min(xyz,axis=0),np.max(xyz,axis=0)],
        residual_quantiles_m=np.quantile(residual,[.05,.5,.95]),
        top_minus_support_m=top['top_center'][2]-plane_z(plane,top['top_center'][:2]))


def box(top,support):
    n,c=top['frame'][:,2],top['top_center']
    denominator=n[2]-np.dot(support[:2],n[:2])
    if denominator<=0: raise TaskBlocked('Invalid support/face relation')
    h=(c[2]-plane_z(support,c[:2]))/denominator
    if not np.isfinite(h) or h<=0: raise TaskBlocked('Nonpositive locally measured thickness')
    return dict(top,height_m=float(h),center=c-n*h/2)


def rectangle(center,basis,size):
    signs=np.array([[-1.,-1.],[1.,-1.],[1.,1.],[-1.,1.]])
    return np.asarray(center)+(signs*np.asarray(size)/2) @ np.asarray(basis).T


def overlap(polygon,size):
    polygon=np.asarray(polygon)
    for axis in (0,1):
        for sign in (-1.,1.):
            if len(polygon)==0: raise TaskBlocked('No finite support overlap')
            out=[]
            previous=polygon[-1]
            dp=size[axis]/2-sign*previous[axis]
            for current in polygon:
                dc=size[axis]/2-sign*current[axis]
                if (dp>=0)!=(dc>=0): out.append(previous+(current-previous)*dp/(dp-dc))
                if dc>=0: out.append(current)
                previous,dp=current,dc
            polygon=np.asarray(out)
    if len(polygon)<3: raise TaskBlocked('Degenerate overlap')
    return polygon


def area(p):
    return abs(float(np.sum(p[:,0]*np.roll(p[:,1],-1)-p[:,1]*np.roll(p[:,0],-1))))/2


def placement(g,r,grasp,pickup,yaw):
    final_box=r['frame'] @ Rotation.from_euler('z',yaw,degrees=True).as_matrix()
    delta=final_box @ g['frame'].T
    final=delta @ pickup
    bottom=r['top_center']+.001*r['frame'][:,2]
    center=bottom+g['height_m']/2*final_box[:,2]
    site=center-delta @ (g['center']-grasp)
    footprint=(rectangle(bottom,final_box[:,:2],g['dimensions'])-r['top_center']) @ r['frame'][:,:2]
    contact=overlap(footprint,r['dimensions'])
    n=r['frame'][:,2]
    gravity_point=center-np.array([0.,0.,np.dot(n,center-r['top_center'])/n[2]])
    return site,final,dict(center=center,final_box_frame=final_box,
        footprint_in_red_frame=footprint,overlap_in_red_frame=contact,
        supported_area_fraction=area(contact)/area(footprint),
        overhang_m=np.maximum(np.max(np.abs(footprint),axis=0)-r['dimensions']/2,0.),
        gravity_projection_in_red_frame=(gravity_point-r['top_center']) @ r['frame'][:,:2])


def move(stage,side,pos,rotation):
    return dict(stage=stage,kind='move',arguments={side+'_target_pos':np.asarray(pos).tolist(),
        side+'_target_rpy':rpy_from_rotation(rotation)})


def segments(steps,state):
    jaws={}
    for side in ('left','right'):
        v=np.asarray(state['arms'][side]['gripper_pos'],dtype=float).reshape(-1)
        if v.size!=1 or not np.isfinite(v[0]): raise TaskBlocked('Invalid planning jaw state')
        jaws[side]=float(v[0])
    result=[]
    for step in steps:
        if step['kind'] in ('open','close'):
            jaws[step['side']]=1. if step['kind']=='open' else 0.
        else:
            result.append(dict(stage=step['stage'],arguments=step['arguments'],gripper_state=dict(jaws)))
    return result


def failure(f):
    return f.get('failing_stage') or next((s.get('stage') for s in f.get('stages',[]) if s.get('status')!='Success'),None)


def build_task(tools):
    station=tools['get_station_info']()
    state=tools['get_planning_state']()
    p,masks,observation=observe(tools,list(QUERIES))
    gt,rt=face(cloud(p,masks,'green_top')),face(cloud(p,masks,'red_top'))
    source,se=local_support(cloud(p,masks,'source_support'),gt)
    table,te=local_support(cloud(p,masks,'red_local_table'),rt)
    print('LOCAL_SUPPORT_EVIDENCE',plain(dict(source=se,red=te)),flush=True)
    g,r=box(gt,source),box(rt,table)
    jaw=station['gripper_geometry']
    axis_name=jaw.get('closing_axis','grasp_x')
    if axis_name not in ('grasp_x','grasp_y'): raise TaskBlocked('Unknown closing axis')
    closing=0 if axis_name=='grasp_x' else 1
    offset=np.asarray(jaw.get('grasp_to_planner_offset_m',[0.,0.,0.]))
    pads=np.asarray(jaw['grip_pad_full_dimensions_m'])
    if offset.shape!=(3,) or pads.shape!=(3,) or not np.isfinite(np.r_[offset,pads]).all():
        raise TaskBlocked('Malformed jaw geometry')
    gap=.0752
    if g['dimensions'][1]>=gap: raise TaskBlocked('Short span exceeds native modeled opening')
    signs=np.array([[a,b,c] for a in (-1.,1.) for b in (-1.,1.) for c in (-1.,1.)])
    corners=g['center']+(signs*np.r_[g['dimensions'],g['height_m']]/2) @ g['frame'].T
    radius=float(np.linalg.norm(np.r_[g['dimensions'],g['height_m']]))/2
    sides=sorted(('left','right'),key=lambda s:float(np.linalg.norm(np.asarray(state['arms'][s]['ee_pos'])-g['top_center'])))
    configs=[(s,sign,tilt) for tilt in (-45.,-30.,30.,0.,-15.,15.,45.) for sign in (1.,-1.) for s in sides]
    trials,blocked,best,best_progress=[],set(),None,-1
    found=False
    options=[(yaw,tilt,clearance) for clearance in (.07,.04)
             for tilt in (0.,30.,-30.) for yaw in (180.,90.,-90.,0.,45.,-45.)]
    for yaw,transport_tilt,clearance in options:
        for side,sign,tilt in configs:
            key=(side,sign,tilt)
            if key in blocked: continue
            axis,z=sign*g['frame'][:,1],-g['frame'][:,2]
            base=(np.column_stack([axis,np.cross(z,axis),z]) if closing==0 else
                  np.column_stack([np.cross(axis,z),axis,z]))
            pickup=Rotation.from_rotvec(axis*np.deg2rad(tilt)).as_matrix() @ base
            grasp=g['top_center']-.35*g['height_m']*g['frame'][:,2]+pickup @ offset
            approach=grasp-.05*pickup[:,2]
            place,final,placed=placement(g,r,grasp,pickup,yaw)
            relative_center=pickup.T @ (g['center']-grasp)
            relative_corners=(corners-grasp) @ pickup
            start=np.asarray(state['arms'][side]['ee_pos'])
            start_rotation=Rotation.from_quat(state['arms'][side]['ee_quat']).as_matrix()
            inward=unit(np.r_[start[:2]-grasp[:2],0.])
            low=grasp+[0.,0.,.035]
            retracted=low+.07*inward
            center_z=max(float(g['top_center'][2]),float(r['top_center'][2]))+radius+clearance
            source_center=retracted+pickup @ relative_center
            source_center[2]=center_z
            raised=source_center-pickup @ relative_center
            target_center=np.r_[placed['center'][:2],center_z]
            transport=Rotation.from_rotvec(final[:,closing]*np.deg2rad(transport_tilt)).as_matrix() @ final
            interp=Slerp([0.,1.],Rotation.from_matrix(np.stack([pickup,transport])))
            transfer=[]
            transfer_poses=[]
            for index,t in enumerate((.25,.5,.75,1.)):
                rotation=interp(t).as_matrix()
                center=(1-t)*source_center+t*target_center
                site=center-rotation @ relative_center
                transfer_poses.append((site,rotation))
                transfer.append(move('transfer_rotate_'+str(index+1),side,site,rotation))
            above=target_center-final @ relative_center
            final_corners=relative_corners @ final.T
            pad_bottom=float(np.sum(np.abs(final[2])*pads)/2)+abs(float(final[2,closing]))*gap/2
            withdrawal=max(.06,float(np.max(final_corners[:,2]))+pad_bottom+.035)
            clear=place+[0.,0.,withdrawal]
            prefix=[dict(stage='open_before_pickup',kind='open',side=side),
                move('source_approach',side,approach,pickup),
                move('source_grasp_short_axis',side,grasp,pickup),
                dict(stage='close_on_green',kind='close',side=side),
                move('short_vertical_lift',side,low,pickup),
                move('retract_before_raising',side,retracted,pickup),
                move('raise_inward_with_payload',side,raised,pickup)]+transfer+[
                move('align_supported_face',side,above,final),
                move('place_on_finite_red',side,place,final),
                dict(stage='release_green',kind='open',side=side),
                move('vertical_clearance',side,clear,final)]
            # The failed target combined home XYZ with placement orientation.
            # After unchanged vertical withdrawal, retrace the measured-space
            # transport waypoints with an OPEN jaw, then request the actual
            # measured start orientation together with its position.
            retreat=[move('retreat_to_transfer_height',side,above,final),
                move('restore_transfer_orientation',side,transfer_poses[-1][0],transport)]
            for i,(position,rotation) in enumerate(reversed(transfer_poses[:-1])):
                retreat.append(move('reverse_transfer_'+str(i+1),side,position,rotation))
            retreat.append(move('clear_target_on_source_side',side,raised,pickup))
            # If a direct return cannot plan, try an intermediate empty-hand
            # orientation change away from the stack. These are offline plans.
            for return_variant in ('direct_measured_pose','intermediate_rotation'):
                ending=[]
                if return_variant=='intermediate_rotation':
                    midpoint=(raised+start)/2
                    midpoint[2]=max(float(raised[2]),float(start[2]))
                    mid_rotation=Slerp([0.,1.],Rotation.from_matrix(np.stack([pickup,start_rotation])))(.5).as_matrix()
                    ending.append(move('empty_hand_return_midpoint',side,midpoint,mid_rotation))
                ending.append(move('return_to_measured_start',side,start,start_rotation))
                steps=prefix+retreat+ending
                feedback=tools['plan_freespace_sequence'](segments(steps,state))
                config=dict(side=side,sign=sign,pickup_tilt_deg=tilt,yaw_deg=yaw,
                    transport_tilt_deg=transport_tilt,clearance_m=clearance,grasp=grasp,place=place,
                    pickup_rotation=pickup,final_rotation=final,placement=placed,
                    grasp_relative_center=relative_center,grasp_relative_corners=relative_corners,
                    source_payload_center=source_center,target_payload_center=target_center,
                    clearance_pose=clear,return_variant=return_variant,
                    return_position=start,return_rotation=start_rotation,
                    payload_rotation_radius_m=radius)
                trials.append(dict(configuration=config,native_feedback=feedback))
                progress=sum(s.get('status')=='Success' for s in feedback.get('stages',[]))
                if best is None or progress>best_progress or feedback.get('success') is True:
                    best,best_progress=(steps,config,feedback),progress
                if feedback.get('success') is True:
                    found=True
                    break
                failed=failure(feedback)
                if failed in ('source_approach','source_grasp_short_axis','short_vertical_lift','retract_before_raising'):
                    blocked.add(key)
                if failed!='return_to_measured_start' or len(trials)>=96: break
            if found or len(trials)>=96: break
        if found or len(trials)>=96: break
        scores={}
        for trial in trials:
            c=trial['configuration']
            k=(c['side'],c['sign'],c['pickup_tilt_deg'])
            scores[k]=max(scores.get(k,-1),sum(s.get('status')=='Success' for s in trial['native_feedback'].get('stages',[])))
        configs=sorted(configs,key=lambda k:-scores.get(k,-1))
    if best is None: raise TaskBlocked('No complete candidate constructed')
    steps,config,feedback=best
    return plain(dict(steps=steps,station=station,planning_start=state,initial_observation=observation,
        local_support_evidence=dict(source=se,red=te),planning_trials=trials,
        candidate_preview_success=feedback.get('success') is True,
        geometry=dict(green=g,red=r,source_support=source,table_plane=table,**config),
        aperture_evidence=dict(native_model_gap_m=gap,short_span_m=g['dimensions'][1],
            modeled_margin_m=gap-g['dimensions'][1],physical_aperture_measured=False),
        previous_review={'latest':'PLAN_FAILED only at clear_outcome_view after placement and vertical-clearance paths passed; zero physical commands.',
            'native_diagnostic':'Failed target paired measured start position with placement orientation; right rotation residual 63.15 degrees.',
            'v6':'FAILED: green beside red at release/withdrawal; mechanism unresolved.',
            'v5_operator':'It fell off the red... while the arm was moving back.',
            'original_native_outcome':'UNVERIFIED'},
        reasoning=[
            'Keep the measured geometry, short-axis grasp, interpolated transport, placement and vertical withdrawal. The latest failure is in empty-hand return planning, not evidence of physical placement.',
            'Replace the incompatible home-position/placement-orientation target with reverse transport waypoints and a final measured start position AND orientation.',
            'Reverse waypoints are newly planned with full-open jaw geometry and preceding predicted endpoints. No loaded-path cache is reused as an empty-hand trajectory.',
            'Finite overlap, overhang and grasp-relative payload geometry remain explicit. Original supporting face is preserved and placement yaw remains free.',
            'Runtime owns fresh full planning before any task action, native caches, motion enablement, recording through parking and shutdown. No physical retry or recovery loop.',
            'Postparking unobstructed observations must establish stacking. Native path success and command completion do not establish retention or placement.']))


def evaluate(tools,task):
    evidence=dict(outcome='UNVERIFIED',operator_observation=None,
        limitations='Contact is inferred from fresh depth. Runner must reassess after parking. Release-time success is provisional; ambiguous support is unverified.')
    try:
        p,masks,obs=observe(tools,['green_top','red','red_top'])
        evidence['observation']=obs
        old=task['geometry']
        red=old['red']
        rc,rf,rs=np.asarray(red['top_center']),np.asarray(red['frame']),np.asarray(red['dimensions'])
        g=face(cloud(p,masks,'green_top'))
        rp,rt=cloud(p,masks,'red'),cloud(p,masks,'red_top')
        if len(rp)<3: raise TaskBlocked('No fresh red depth')
        uv=(rp-rc) @ rf[:,:2]
        at_target=bool(np.mean(np.all(np.abs(uv)<=rs/2+.008,axis=1))>.8)
        method='fresh red top'
        if len(rt)>=3:
            support=fit_support(rt)
        else:
            support=np.asarray(red['top_plane']).copy()
            residual=rp[:,2]-plane_z(support,rp[:,:2])
            edge=float(np.quantile(residual,.95))
            boundary=np.min(np.abs(np.abs(uv)-rs/2),axis=1)
            corroborated=bool(at_target and abs(edge)<=.006 and np.ptp(rp[:,2])>=.35*red['height_m'] and
                np.mean(boundary<=.008)>.6 and np.max(np.ptp(uv,axis=0))>=.5*min(rs))
            evidence['red_edge_evidence']=dict(edge_residual_m=edge,corroborated=corroborated)
            if not corroborated: raise TaskBlocked('Hidden support lacks fresh side/upper-edge corroboration')
            support[2]+=edge
            method='historical slope corroborated by fresh side and upper edge'
        n=g['frame'][:,2]
        bottom=g['top_center']-old['green']['height_m']*n
        corners=rectangle(bottom,g['frame'][:,:2],g['dimensions'])
        poly=overlap((corners-rc) @ rf[:,:2],rs)
        contact=rc+poly @ rf[:,:2].T
        bottom_plane=np.asarray(g['top_plane']).copy()
        bottom_plane[2]-=old['green']['height_m']/n[2]
        gaps=plane_z(bottom_plane,contact[:,:2])-plane_z(support,contact[:,:2])
        center_uv=(bottom-rc) @ rf[:,:2]
        checks=dict(centered=bool(np.all(np.abs(center_uv)<rs*.25)),
            parallel_to_support=bool(np.dot(n,unit([-support[0],-support[1],1.]))>np.cos(np.deg2rad(8))),
            original_face=bool(np.allclose(np.sort(g['dimensions']),np.sort(old['green']['dimensions']),rtol=.20,atol=.005)),
            red_at_target=at_target,red_height_consistent=bool(abs(plane_z(support,rc[:2])-rc[2])<=.008),
            contact_consistent=bool(abs(float(np.min(gaps)))<=.006 and np.max(gaps)<=.010))
        success=bool(all(checks.values()))
        evidence.update(outcome='PHYSICAL_SUCCESS' if success else 'UNVERIFIED',checks=checks,
            green_final=g,support_plane=support,support_method=method,center_offset_m=center_uv,
            finite_overlap=poly,gap_range_m=[np.min(gaps),np.max(gaps)])
        return dict(success=success,evidence=plain(evidence))
    except (TaskBlocked,ValueError,KeyError,np.linalg.LinAlgError) as exc:
        evidence['reason']=type(exc).__name__+': '+str(exc)
        return dict(success=False,evidence=plain(evidence))
