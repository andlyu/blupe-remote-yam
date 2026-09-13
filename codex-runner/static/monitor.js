(() => {
  const baseRender = window.render;
  const cameraGrid = document.getElementById("cameraGrid");
  const cameraState = document.getElementById("cameraState");
  const simState = document.getElementById("simState");
  const canvas = document.getElementById("armSim");
  const context = canvas.getContext("2d");
  let latest = null;
  let feedbackKey = "";
  let feedbackReceivedAt = 0;

  function cameras(observation) {
    return observation && observation.images && typeof observation.images === "object" ? observation.images : {};
  }

  function renderCameras(observation) {
    const entries = Object.entries(cameras(observation)).filter(([name, image]) => ["left", "top", "right"].includes(name) && image && image.url);
    const signature = JSON.stringify(entries.map(([name, image]) => [name, image.url]));
    if (cameraGrid.dataset.signature === signature) return;
    cameraGrid.dataset.signature = signature;
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "camera-empty";
      empty.textContent = "WAITING FOR SERVER FRAMES";
      cameraGrid.replaceChildren(empty);
      return;
    }
    const tiles = entries.map(([name, image]) => {
      const tile = document.createElement("figure");
      tile.className = "camera-tile";
      const frame = document.createElement("img");
      frame.alt = `${name} camera feedback from the Session API`;
      const proxyUrl = `/api/monitor/cameras/${name}`;
      frame.onload = () => {
        tile.classList.remove("failed"); updateFreshness();
        // JPEG responses finish loading; MJPEG streams remain open until stopped.
        setTimeout(() => { if (frame.isConnected) frame.src = proxyUrl; }, 200);
      };
      frame.onerror = () => {
        tile.classList.add("failed");
        updateFreshness();
        setTimeout(() => { if (frame.isConnected) frame.src = proxyUrl; }, 1000);
      };
      frame.src = proxyUrl;
      const caption = document.createElement("figcaption");
      caption.textContent = `${name} / SERVER FEEDBACK`;
      tile.append(frame, caption);
      return tile;
    });
    cameraGrid.replaceChildren(...tiles);
  }

  // Exact joint frames from assets/yam_bimanual/yam_bimanual.urdf. This
  // kinematic twin is driven only by Session API observations.
  const armFrames = [
    {xyz:[0,0,.067],rpy:[0,0,-1.570800569],axis:1},
    {xyz:[-.0329,.02,.0455],rpy:[1.570800327,0,-1.570800327],axis:1},
    {xyz:[.264,.000000408,-.06375],rpy:[-3.14159,0,1.570800569],axis:1},
    {xyz:[.0600003,-.244999,-.00205],rpy:[0,0,-3.14159],axis:1},
    {xyz:[-.0403003,.0703851,-.0323887],rpy:[.000002327,-1.570792084,-3.141592327],axis:1},
    {xyz:[.00000024,-.0419481,.0404996],rpy:[1.570794327,-1.570792327,3.141590654],axis:-1}
  ];
  const identity = () => [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
  const multiply = (a,b) => {
    const out = Array(16).fill(0);
    for(let r=0;r<4;r+=1) for(let c=0;c<4;c+=1) for(let k=0;k<4;k+=1) out[r*4+c]+=a[r*4+k]*b[k*4+c];
    return out;
  };
  const translation = ([x,y,z]) => [1,0,0,x, 0,1,0,y, 0,0,1,z, 0,0,0,1];
  const rotationX = a => {const c=Math.cos(a),s=Math.sin(a);return [1,0,0,0,0,c,-s,0,0,s,c,0,0,0,0,1]};
  const rotationY = a => {const c=Math.cos(a),s=Math.sin(a);return [c,0,s,0,0,1,0,0,-s,0,c,0,0,0,0,1]};
  const rotationZ = a => {const c=Math.cos(a),s=Math.sin(a);return [c,-s,0,0,s,c,0,0,0,0,1,0,0,0,0,1]};
  const frame = ({xyz,rpy}) => multiply(translation(xyz),multiply(rotationZ(rpy[2]),multiply(rotationY(rpy[1]),rotationX(rpy[0]))));
  const point = matrix => [matrix[3],matrix[7],matrix[11]];

  function yamChain(side,joints){
    let transform=identity();
    const points=[];
    armFrames.forEach((spec,index)=>{
      const origin=index===0?[spec.xyz[0],side==="left"?.35:-.35,spec.xyz[2]]:spec.xyz;
      transform=multiply(transform,frame({...spec,xyz:origin}));
      points.push(point(transform));
      transform=multiply(transform,rotationZ(Number(joints[index])*Math.PI/180*spec.axis));
    });
    points.push(point(multiply(transform,translation([0,0,-.1347]))));
    return points;
  }

  function project([x,y,z]){
    const yaw=-Math.PI/4,pitch=Math.PI/7,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
    const x1=x*cy-y*sy,y1=x*sy+y*cy;
    return {x:canvas.width/2+x1*430,y:canvas.height-52-(z*cp-y1*sp)*430,depth:y1*cp+z*sp};
  }

  function drawFloor(){
    context.strokeStyle="rgba(245,241,230,.11)";context.lineWidth=1;
    for(let n=-5;n<=5;n+=1){
      [[[-.55,n*.1,0],[.55,n*.1,0]],[[n*.1,-.55,0],[n*.1,.55,0]]].forEach(([a,b])=>{
        const pa=project(a),pb=project(b);context.beginPath();context.moveTo(pa.x,pa.y);context.lineTo(pb.x,pb.y);context.stroke();
      });
    }
  }

  function drawYam(left,right){
    const arms=[{color:"#c6f432",points:yamChain("left",left)},{color:"#f0a52b",points:yamChain("right",right)}];
    const segments=[];
    arms.forEach(arm=>arm.points.slice(0,-1).forEach((start,index)=>segments.push({arm,index,a:project(start),b:project(arm.points[index+1])})));
    segments.sort((a,b)=>(a.a.depth+a.b.depth)-(b.a.depth+b.b.depth));
    const leftBase=project([0,.35,0]),rightBase=project([0,-.35,0]);
    context.strokeStyle="#9ea99f";context.lineWidth=12;context.beginPath();context.moveTo(leftBase.x,leftBase.y);context.lineTo(rightBase.x,rightBase.y);context.stroke();
    segments.forEach(({arm,index,a,b})=>{
      context.strokeStyle="rgba(0,0,0,.38)";context.lineWidth=Math.max(6,18-index*1.8);context.lineCap="round";
      context.beginPath();context.moveTo(a.x+3,a.y+4);context.lineTo(b.x+3,b.y+4);context.stroke();
      context.strokeStyle=arm.color;context.lineWidth=Math.max(4,13-index*1.5);
      context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();
      context.fillStyle="#13221c";context.strokeStyle=arm.color;context.lineWidth=3;context.beginPath();context.arc(a.x,a.y,7-index*.45,0,Math.PI*2);context.fill();context.stroke();
      if(index===5){context.fillStyle=arm.color;context.beginPath();context.arc(b.x,b.y,5,0,Math.PI*2);context.fill()}
    });
  }

  function drawObservation(observation) {
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#13221c";
    context.fillRect(0, 0, canvas.width, canvas.height);
    drawFloor();
    if (!observation) {
      context.fillStyle = "#f5f1e6";
      context.font = "16px monospace";
      context.fillText("WAITING FOR SERVER JOINT STATE", 184, 184);
      return;
    }
    const left = observation.left_joints_deg || [];
    const right = observation.right_joints_deg || [];
    if (left.length !== 6 || right.length !== 6) {
      drawObservation(null);
      return;
    }
    drawYam(left,right);
    context.fillStyle="#f5f1e6";context.font="700 12px monospace";context.fillText("YAM BIMANUAL URDF / SERVER-OBSERVED POSE",18,24);
  }

  function renderFeedback(observation) {
    latest = observation || null;
    renderCameras(latest);
    const key = latest ? `${latest.episode_id || ""}:${latest.step_id ?? ""}:${latest.observed_at || ""}` : "";
    if (key && key !== feedbackKey) {
      feedbackKey = key;
      feedbackReceivedAt = Date.now();
    }
    drawObservation(latest);
    updateFreshness();
  }

  function updateFreshness() {
    const age = feedbackReceivedAt ? Date.now() - feedbackReceivedAt : Infinity;
    const label = age < 3000 ? "LIVE FEEDBACK" : feedbackReceivedAt ? "STALE" : "NO SIGNAL";
    const klass = age < 3000 ? "live" : feedbackReceivedAt ? "stale" : "";
    const frames = [...cameraGrid.querySelectorAll(".camera-tile")];
    const cameraLive = frames.length > 0 && frames.some(tile => !tile.classList.contains("failed"));
    cameraState.textContent = cameraLive ? label : "NO SIGNAL";
    cameraState.className = `feedback-badge ${cameraLive ? klass : ""}`;
    simState.textContent = latest && (latest.left_joints_deg || []).length === 6 && (latest.right_joints_deg || []).length === 6 ? label : "NO STATE";
    simState.className = `feedback-badge ${simState.textContent === "NO STATE" ? "" : klass}`;
  }

  window.render = state => {
    baseRender(state);
    renderFeedback(state.last_observation);
  };
  drawObservation(null);
  setInterval(updateFreshness, 500);
})();
