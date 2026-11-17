# SPDX-License-Identifier: GPL-3.0-or-later
"""blemish.py - retoque automatico de imperfecciones de piel (granos, puntos negros, rojeces pequenas).
Ultimo paso de retoque del motor Ektar, antes del grano. Trabaja en OkLab (L, a, b) dentro de la mascara de piel."""
import numpy as np, cv2
F32=np.float32
def gb(x,s): return cv2.GaussianBlur(x,(0,0),float(s))
def _disk(r):
    k=max(3,int(2*r)|1); return cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k))

def heal_blemishes(lab, mask, fw, strength=1.0, dbg=None, maxn=300):
    """Spot healing automatico dentro de la mascara de piel: granos/rojeces (a* alto) y puntos negros (L bajo)
    detectados como blobs en espacio-escala (DoG multi-escala relativo a la piel vecina), curados con un
    'pincel corrector' (baja frecuencia reconstruida del borde + detalle/poros de un parche donante)."""
    H,W=mask.shape
    if float(mask.max())<0.3: return dict(blemishes=0)
    ys,xs=np.where(mask[::8,::8]>0.3)
    rmin=max(2.5, 0.0028*fw); rmax=max(rmin*2, 0.015*fw)
    pad=int(4*rmax)+16
    y0,y1=max(ys.min()*8-pad,0),min(ys.max()*8+pad,H); x0,x1=max(xs.min()*8-pad,0),min(xs.max()*8+pad,W)
    L=np.ascontiguousarray(lab[y0:y1,x0:x1,0]); A=np.ascontiguousarray(lab[y0:y1,x0:x1,1]); Bc=np.ascontiguousarray(lab[y0:y1,x0:x1,2])
    M0=np.ascontiguousarray(mask[y0:y1,x0:x1]).astype(F32)
    se=_disk(1.2*rmax)
    M=np.maximum(M0, cv2.erode(cv2.dilate(M0,se),se))     # rellena huecos (el grano rojo sale del calificador)
    sig=np.geomspace(rmin/1.41, rmax/1.41, 6).astype(F32)
    dark=np.zeros_like(L); red=np.zeros_like(L); sd=np.zeros_like(L); sr=np.zeros_like(L)
    white=np.zeros_like(L); sw=np.zeros_like(L)
    for i_,s in enumerate(sig):
        g1L=gb(L,s); dL=gb(g1L,s*2.83)-g1L      # = G(3s)-G(s): >0 punto mas oscuro que su entorno
        g1A=gb(A,s); dA=g1A-gb(g1A,s*2.83)      # >0 punto mas rojo
        u=dL>dark; dark[u]=dL[u]; sd[u]=s
        u=dA>red; red[u]=dA[u]; sr[u]=s
        if i_<3:                                 # puntos blancos (pustulas): solo escalas pequenas
            u=(-dL)>white; white[u]=-dL[u]; sw[u]=s
    del g1L,g1A,dL,dA
    inside=M>0.28
    madL=float(np.median(np.abs(dark[inside])))*1.48+1e-4
    madA=float(np.median(np.abs(red[inside])))*1.48+1e-4
    tL=max(4.0*madL, 0.010); tA=max(4.0*madA, 0.0040)
    sL=dark/tL; sA=red/tA
    # punto blanco solo cuenta si esta sobre base inflamada (rojez a escala mayor): no brillos ni gotas
    Abig=gb(A,2.0*rmax)-gb(A,6.0*rmax)
    sW=(white/tL)*np.clip(Abig/(1.2*tA),0,1)
    u=sW>sA; sA=np.where(u,sW,sA); sr=np.where(u,sw,sr)
    score=np.maximum(np.maximum(sL,sA),0.8*(np.maximum(sL,0)+np.maximum(sA,0))); sbest=np.where(sL>=sA,sd,sr)
    score*=inside
    pk=(score>=cv2.dilate(score,_disk(1.5*rmin)))&(score>1.1)
    # zonas de barba/vello: muchos puntos oscuros pequenos juntos -> no tocar los puntos solo-oscuros
    pkd=(score>=cv2.dilate(score,_disk(1.2*rmin)))&(sL>0.6)&(sA<0.6*sL)&inside
    win=max(9,int(0.06*fw))|1
    dens=cv2.boxFilter(pkd.astype(F32),-1,(win,win),normalize=False)
    py,px=np.nonzero(pk)
    order=np.argsort(-score[py,px])[:maxn*3]
    # estructura: gradiente a escala rmin (bordes de labios, fosas, ojos, pelo)
    gs=gb(L,rmin)
    taken=np.zeros(L.shape,np.uint8)
    comps=[]; rej=dict(size=0,shape=0,ring=0,mole=0,dup=0,asym=0)
    for j in order:
        cy,cx=int(py[j]),int(px[j])
        if taken[cy,cx]: rej["dup"]+=1; continue
        if dens[cy,cx]>=5 and sA[cy,cx]<1.0: rej["stubble"]=rej.get("stubble",0)+1; continue
        r=float(1.41*sbest[cy,cx]); pv=float(score[cy,cx])
        def R(k):
            rej[k]=rej.get(k,0)+1
        m=int(5.5*r)+8
        ya,yb,xa,xb=max(cy-m,0),min(cy+m+1,L.shape[0]),max(cx-m,0),min(cx+m+1,L.shape[1])
        sw=score[ya:yb,xa:xb]
        yy,xx=np.ogrid[ya-cy:yb-cy,xa-cx:xb-cx]
        cand=((sw>max(0.3*pv,0.8))&((yy*yy+xx*xx)<=(1.7*r)**2)).astype(np.uint8)
        n,lb=cv2.connectedComponents(cand,connectivity=8)
        reg=(lb==lb[cy-ya,cx-xa])&(cand>0)
        area=int(reg.sum())
        if area<4: R("size"); continue
        dia=2*np.sqrt(area/np.pi)
        if dia>2.2*rmax: R("size"); continue
        ry,rx=np.nonzero(reg)
        hh,ww=int(np.ptp(ry))+1,int(np.ptp(rx))+1
        if max(hh,ww)>2.5*min(hh,ww)+3 or area/(hh*ww)<0.38: R("shape"); continue
        reg8=reg.astype(np.uint8)
        ring=(cv2.dilate(reg8,_disk(max(2.0*dia,6)))>0)&~(cv2.dilate(reg8,_disk(max(1.0*dia,4)))>0)
        if ring.sum()<8: R("ring"); continue
        Mw=M0[ya:yb,xa:xb]
        if float(np.mean(Mw[ring]>0.25))<0.85: R("ring"); continue
        ry_,rx_=np.nonzero(ring)
        Lr=gs[ya:yb,xa:xb][ring]
        Am=np.stack([np.ones_like(ry_),ry_,rx_],1).astype(F32)
        coef,*_=np.linalg.lstsq(Am,Lr,rcond=None)
        Lr=Lr-Am@coef                      # quita el sombreado suave (plano) del anillo
        if float(np.percentile(Lr,90)-np.percentile(Lr,10))>0.06: R("ring"); continue
        Ar=A[ya:yb,xa:xb][ring]
        if float(np.percentile(Ar,90)-np.percentile(Ar,10))>0.036: R("ring"); continue
        Lw=L[ya:yb,xa:xb]; Aw=A[ya:yb,xa:xb]
        dl=float(np.median(L[ya:yb,xa:xb][ring])-np.percentile(Lw[reg],5))
        da=float(np.percentile(Aw[reg],95)-np.median(Aw[ring]))
        # simetria: el punto debe destacar contra TODO su entorno (8 sectores), no ser el final de un borde
        ang=np.arctan2(ry_-(cy-ya),rx_-(cx-xa)); sec=((ang+np.pi)/(2*np.pi)*8).astype(int)%8
        isdark=sL[cy,cx]>=sA[cy,cx]
        vals=(L[ya:yb,xa:xb][ring]-float(np.percentile(Lw[reg],5))) if isdark else (float(np.percentile(Aw[reg],95))-A[ya:yb,xa:xb][ring])
        sm_=[float(np.median(vals[sec==k])) for k in range(8) if (sec==k).sum()>2]
        if len(sm_)<6 or min(sm_)<=0 or min(sm_)<0.3*max(sm_): R("asym"); continue
        # oscuro sin rojez y grande = sombra (bajo lobulo, pliegue) o lunar -> no tocar; los puntos negros son pequenos
        if dia>0.011*fw and da<max(0.008,0.2*dl): R("mole"); continue
        # lunares (oscuros, bien definidos, poco rojos, de cierto tamano) -> se conservan
        if dia>0.008*fw and dl>0.06 and da<0.010: R("mole"); continue
        taken[ya:yb,xa:xb]|=cv2.dilate(reg8,_disk(max(0.6*dia,3)))
        lo_,hi_=float(np.percentile(L[ya:yb,xa:xb][ring],5)),float(np.percentile(L[ya:yb,xa:xb][ring],92))
        comps.append((ya,yb,xa,xb,reg8,max(dia/2,2.0),lo_,hi_))
        if len(comps)>=maxn: break
    info=dict(blem_peaks=int(pk.sum()), blemishes=len(comps), blem_rej=rej)
    excl=cv2.dilate(taken,np.ones((5,5),np.uint8))
    core=np.zeros_like(taken)
    for (ya,yb,xa,xb,reg8,r,lo_,hi_) in comps: core[ya:yb,xa:xb]|=reg8
    chans=[L,A,Bc]; out=[c.copy() for c in chans]
    heal=np.zeros_like(L)
    for (ya,yb,xa,xb,reg8,r,lo_,hi_) in comps:
        # ventana ampliada para la reconstruccion
        m=int(4*r)+8
        Ya,Yb,Xa,Xb=max(ya-m,0),min(yb+m,L.shape[0]),max(xa-m,0),min(xb+m,L.shape[1])
        loc=np.zeros((Yb-Ya,Xb-Xa),np.uint8); loc[ya-Ya:yb-Ya,xa-Xa:xb-Xa]=reg8
        hole=cv2.dilate(loc,_disk(max(1.3*r,3))).astype(F32)
        wt=(1-hole)*(1-excl[Ya:Yb,Xa:Xb])*np.clip(M[Ya:Yb,Xa:Xb]*2,0,1)
        if wt.sum()<20: continue
        sl=max(1.5,1.4*r)
        den=gb(wt,sl)+1e-6
        hb=hole>0
        ringm=(wt>0)&(gb(hole,1.5*r)>0.02)
        Lring=float(L[Ya:Yb,Xa:Xb][ringm].mean()) if ringm.any() else float(L[Ya:Yb,Xa:Xb][wt>0].mean())
        best=None
        for dist in (2.6,3.4,4.5,6.0):
            for ang in np.linspace(0,2*np.pi,12,endpoint=False):
                dx=int(round(np.cos(ang)*dist*r)); dy=int(round(np.sin(ang)*dist*r))
                sy0,sy1,sx0,sx1=Ya+dy,Yb+dy,Xa+dx,Xb+dx
                if sy0<0 or sx0<0 or sy1>L.shape[0] or sx1>L.shape[1]: continue
                if (core[sy0:sy1,sx0:sx1][hb]>0).any(): continue
                if float(M0[sy0:sy1,sx0:sx1][hb].min())<0.2: continue
                Ld=L[sy0:sy1,sx0:sx1][hb]
                cost=abs(float(Ld.mean())-Lring)*3+float(Ld.std())
                if best is None or cost<best[0]: best=(cost,dy,dx)
            if best is not None: break
        feather=np.clip(gb(hole,max(1.0,0.6*r))*1.6,0,1)*F32(strength)
        for c,ch in enumerate(chans):
            win=ch[Ya:Yb,Xa:Xb]
            low=gb(win*wt,sl)/den
            if best is not None:
                _,dy,dx=best
                dwin=ch[Ya+dy:Yb+dy,Xa+dx:Xb+dx]
                det=(dwin-gb(dwin,sl))*(1.0 if c==0 else 0.5)
            else:
                det=0.0
            new=low+det
            if c==0: new=np.clip(new,lo_,hi_)    # nunca crear un punto mas claro/oscuro que la piel vecina
            o=out[c][Ya:Yb,Xa:Xb]
            o+=feather*(new-o)
        # halo inflamado alrededor: bajar el exceso de a* respecto a la piel de referencia (escala 3r)
        big=cv2.dilate(loc,_disk(max(3.0*r,5))).astype(F32)
        wb=(1-big)*np.clip(M[Ya:Yb,Xa:Xb]*2,0,1)
        if wb.sum()>20:
            aref=gb(out[1][Ya:Yb,Xa:Xb]*wb,2.5*r)/(gb(wb,2.5*r)+1e-6)
            fb=np.clip(gb(big,r)*1.3,0,1)*F32(0.6*strength)
            oa=out[1][Ya:Yb,Xa:Xb]
            oa-=fb*np.maximum(oa-aref,0)
        heal[Ya:Yb,Xa:Xb]=np.maximum(heal[Ya:Yb,Xa:Xb],feather)
    for c in range(3): lab[y0:y1,x0:x1,c]=out[c]
    if dbg is not None:
        full=np.zeros((H,W),F32); full[y0:y1,x0:x1]=heal; dbg["heal"]=full
    return info
