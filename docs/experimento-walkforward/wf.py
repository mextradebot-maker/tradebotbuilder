import pandas as pd, numpy as np
from math import sqrt
from statistics import NormalDist
d=pd.read_csv('../experimento-stop/exp_stop_setups.csv')
d=d[d.factor==0.5].copy()
d['ts']=pd.to_datetime(d.ts,utc=True); d=d.sort_values('ts').reset_index(drop=True)
SP={'EURUSD':7e-5,'GBPUSD':9e-5,'USDJPY':6.7e-5,'AUDJPY':1e-4,'XAUUSD':1.5e-4,'US30':6e-5,'BRENTUSD':4e-4,'BTCUSD':5e-4}
def costR(df,mult=1.0): return df.simbolo.map(SP)*mult/df.riesgo_pct
d['costo']=costR(d)
d['q']=d.ts.dt.to_period('Q')
print("setups",len(d),"periodo",d.ts.min().date(),d.ts.max().date())
print("costo medio en R por TF:",d.groupby('temporalidad').costo.mean().round(3).to_dict())
Z=NormalDist()
def decide(hist,row,metodo,umbral=0.5):
    h=hist[(hist.temporalidad==row.temporalidad)]
    g=h[h.direccion==row.direccion]
    cell=g[g.simbolo==row.simbolo]
    if metodo=='todo': return True
    if metodo=='naive':   # lo que hariamos con n>=10 y expectativa>0
        return len(cell)>=10 and cell.r.mean()>0
    if metodo=='grupo':   # solo filtro por (TF,direccion): expectativa del grupo en el pasado >0
        return len(g)>=30 and g.r.mean()>0
    if metodo in('encogido','encogido80'):
        if len(g)<30: return False
        mu=g.r.mean(); s2=g.r.var()
        cells=[c.r for _,c in g.groupby('simbolo') if len(c)>=5]
        if len(cells)<3: return False
        ses=[(c.var()/len(c)) for c in cells]; means=[c.mean() for c in cells]
        tau2=max(np.var(means,ddof=1)-np.mean(ses),0.0025)
        n=len(cell); se2=s2/max(n,1)
        w=tau2/(tau2+se2) if n>0 else 0
        post=mu+w*((cell.r.mean() if n>0 else mu)-mu)
        postvar=1/(1/tau2+ (n/s2 if n>0 else 0))
        # costo medio esperado de esta celda
        c=cell.costo.mean() if n>0 else row.costo
        neto=post-c
        if metodo=='encogido': return neto>0
        return 1-Z.cdf((0-neto)/sqrt(postvar))>=0.8
res={}
metodos=['todo','naive','grupo','encogido','encogido80']
qs=sorted(d.q.unique())
rows=[]
for q in qs:
    t0=q.start_time.tz_localize('UTC') if q.start_time.tzinfo is None else q.start_time
    hist=d[d.ts<t0]
    test=d[d.q==q]
    if len(hist)<150: continue
    for _,r in test.iterrows():
        rows.append((r.name,{m:decide(hist,r,m) for m in metodos}))
oos=d.loc[[i for i,_ in rows]].copy()
for m in metodos: oos[m]=[x[m] for _,x in rows]
print("\nFUERA DE MUESTRA (decisiones solo con datos anteriores). setups evaluados:",len(oos),"desde",oos.ts.min().date())
def resumen(mask,cost_mult):
    s=oos[mask]; 
    if len(s)==0: return dict(n=0)
    neto=s.r-s.costo*cost_mult
    cum=neto.cumsum().values; dd=(np.maximum.accumulate(cum)-cum).max()
    return dict(n=len(s),pct=round(len(s)/len(oos)*100),bruto=round(s.r.mean(),3),neto=round(neto.mean(),3),R_neto=round(neto.sum(),1),gana=round((s.r>0).mean()*100),dd_R=round(dd,1),se=round(neto.std()/sqrt(len(s)),3))
for mult in (1.0,0.5,2.0):
    print(f"\n--- costos x{mult} ---")
    print(pd.DataFrame({m:resumen(oos[m],mult) for m in metodos}).T.to_string())
print("\nPor temporalidad, costos x1 (neto por operacion, n):")
for tf in oos.temporalidad.unique():
    sub=oos[oos.temporalidad==tf]
    out={}
    for m in metodos:
        s=sub[sub[m]]; out[m]=(round((s.r-s.costo).mean(),3) if len(s) else None,len(s))
    print(tf,out)
# maldicion del ganador: seleccion in-sample (todo el periodo) vs OOS
print("\nMALDICION DEL GANADOR (celdas simbolo x TF x direccion):")
a=d[d.ts<pd.Timestamp('2023-01-01',tz='UTC')]; b=d[d.ts>=pd.Timestamp('2023-01-01',tz='UTC')]
keys=['simbolo','temporalidad','direccion']
ga=a.groupby(keys).r.agg(['mean','size']).query('size>=10')
top=ga[ga['mean']>0.15].index
gb=b.set_index(keys).loc[b.set_index(keys).index.isin(top)]
print("celdas con exp>+0.15R antes de 2023:",len(top),"| su exp media ANTES:",round(ga.loc[top,'mean'].mean(),3),"| DESPUES (2023+):",round(gb.r.mean(),3),"n=",len(gb))
# simultaneidad
d['dia']=d.ts.dt.date
c=d.groupby(['temporalidad','dia']).size()
print("\nSIMULTANEIDAD: dias con >=3 setups el mismo dia (de los dias con setups):",{tf:round((c[tf]>=3).mean()*100) for tf in c.index.get_level_values(0).unique()},"| max en un dia:",c.groupby(level=0).max().to_dict())
usd=d[d.simbolo.isin(['EURUSD','GBPUSD','USDJPY'])]
cu=usd.groupby(['temporalidad','dia','direccion']).size()
print("grupo USD (EURUSD,GBPUSD,USDJPY) mismo dia y direccion con >=2 setups:",{tf:round((cu[tf]>=2).mean()*100) for tf in cu.index.get_level_values(0).unique()})

print("\n=== DIAGNOSTICO (fuera de muestra, todos los setups, bruto por operacion) ===")
for col in ['simbolo','direccion','tipo','temporalidad']:
    g=oos.groupby(col).r.agg(['mean','size']).round(3); g['se']=(oos.groupby(col).r.std()/np.sqrt(g['size'])).round(3); print(g.T.to_string(),'\n')
sel=oos[oos.encogido]
print("Celdas que eligio 'encogido' (n, bruto OOS):")
print(sel.groupby(['simbolo','temporalidad','direccion']).r.agg(['size','mean']).round(2).sort_values('size',ascending=False).head(10).to_string())
# correlacion de persistencia: media por celda antes vs despues de 2023
a=d[d.ts<pd.Timestamp('2023-01-01',tz='UTC')].groupby(keys).r.agg(['mean','size']).query('size>=10')
b=d[d.ts>=pd.Timestamp('2023-01-01',tz='UTC')].groupby(keys).r.agg(['mean','size']).query('size>=10')
j=a.join(b,lsuffix='_a',rsuffix='_b',how='inner')
print("\nPERSISTENCIA: correlacion entre expectativa de cada celda antes de 2023 y despues:",round(j.mean_a.corr(j.mean_b),3),"con",len(j),"celdas")
