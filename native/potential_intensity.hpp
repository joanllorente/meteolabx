// Intensidad potencial de Bister y Emanuel (2002), traducida de tcpyPI 1.4
// (`pi.py` y `utilities.py`, Gilford 2021), que a su vez es el pcmin.m de
// Kerry Emanuel. Licencia MIT en tcpyPI-LICENSE.
//
// Se conservan los valores por defecto de tcpyPI: ascenso reversible,
// calentamiento disipativo, Ck/Cd = 0,9, reducción a 10 m de 0,8, perfil
// ignorado por encima de 50 hPa y cualquier hueco en la columna da NaN. La
// única diferencia es de entrada: los niveles bajo el suelo (presión del nivel
// mayor que la de superficie) se quitan antes de empezar, porque el IFS los
// extrapola y no son aire. La columna que queda empieza en el primer nivel
// sobre la superficie.
namespace pi_detail {
constexpr double CPD=1005.7, CPV=1870.0, CL=2500.0, CPVMCL=CPV-CL, RV=461.5, RD=287.04;
constexpr double EPS=RD/RV, ALV0=2.501e6, A_LCL=1669.0, B_LCL=122.0, B_EYE=2.0;
constexpr double CKCD=0.9, V_REDUC=0.8, PTOP=50.0;

inline double es_cc(double tc) { return 6.112*std::exp(17.67*tc/(243.5+tc)); }
inline double lv(double tc) { return ALV0+CPVMCL*tc; }
inline double ev(double r,double p) { return r*p/(EPS+r); }
inline double rv(double e,double p) { return EPS*e/(p-e); }
inline double trho(double t,double rt,double r) { return t*(1.+r/EPS)/(1.+rt); }
inline double entropy(double t,double r,double p) {
    double e=ev(r,p), rh=std::min(e/es_cc(t-273.15),1.0);
    return (CPD+r*CL)*std::log(t)-RD*std::log(p-e)+lv(t-273.15)*r/t-r*RV*std::log(rh);
}

// Temperatura saturada con entropía `s` a la presión `p`, por Newton-Raphson.
// Devuelve false si no converge, como el IFLAG=2 de tcpyPI.
inline bool temperature_from_entropy(double s,double p,double rp,double t0,double& tg,double& rg) {
    double tnew=t0;
    rg=rv(es_cc(t0-273.15),p);
    tg=0;
    int nc=0;
    while (std::abs(tnew-tg)>0.001) {
        tg=tnew;
        double tc=tg-273.15, enew=es_cc(tc);
        rg=rv(enew,p);
        ++nc;
        double alv=lv(tc);
        double sl=(CPD+rp*CL+alv*alv*rg/(RV*tg*tg))/tg;
        double em=ev(rg,p);
        double sg=(CPD+rp*CL)*std::log(tg)-RD*std::log(p-em)+alv*rg/tg;
        tnew=tg+(nc<3?0.3:1.0)*(s-sg)/sl;
        if (nc>500||enew>p-1) return false;
    }
    return true;
}

struct Cape { double cape, tob, lnb; int flag; };

// CAPE de una parcela (tp K, rp g/g, pp hPa) en el perfil t, r, p, del suelo
// hacia arriba y sin huecos. Es `cape()` de tcpyPI con miss_handle=1.
inline Cape cape(double tp,double rp,double pp,const std::vector<double>& T,
                 const std::vector<double>& R,const std::vector<double>& P) {
    // N = argmin |P − ptop|; el nivel N queda fuera, como en el original.
    size_t n=0;
    for (size_t k=1;k<P.size();++k) if (std::abs(P[k]-PTOP)<std::abs(P[n]-PTOP)) n=k;
    size_t nlvl=n;
    if (nlvl<3||P[2]-P[1]>0) return {0,NaN,NaN,0};
    if (rp<1e-6||tp<200) return {0,NaN,NaN,0};
    double rh=std::min(ev(rp,pp)/es_cc(tp-273.15),1.0);
    double s=entropy(tp,rp,pp);
    double plcl=pp*std::pow(rh,tp/(A_LCL-B_LCL*rh-tp));
    std::vector<double> dif(nlvl,0.0);
    for (size_t j=0;j<nlvl;++j) {
        if (P[j]>=plcl) {
            double tg=tp*std::pow(P[j]/pp,RD/CPD);
            dif[j]=trho(tg,rp,rp)-trho(T[j],R[j],R[j]);
        } else {
            double tg,rg;
            if (!temperature_from_entropy(s,P[j],rp,T[j],tg,rg)) return {0,T[0],P[0],2};
            // Ascenso reversible: el agua condensada sigue en la parcela.
            dif[j]=trho(tg,rp,rg)-trho(T[j],R[j],R[j]);
        }
    }
    size_t inb=0;
    for (size_t j=nlvl-1;j>0;--j) if (dif[j]>0) inb=std::max(inb,j);
    if (inb==0) return {0,T[0],0,1};
    double pa=0,na=0;
    for (size_t j=1;j<=inb;++j) {
        double pfac=RD*(dif[j]+dif[j-1])*(P[j-1]-P[j])/(P[j]+P[j-1]);
        pa+=std::max(pfac,0.0);
        na-=std::min(pfac,0.0);
    }
    double pfac=RD*(pp-P[0])/(pp+P[0]);
    pa+=pfac*std::max(dif[0],0.0);
    na-=pfac*std::min(dif[0],0.0);
    double pat=0,tob=T[inb],lnb=P[inb];
    if (inb<nlvl-1) {
        double pinb=(P[inb+1]*dif[inb]-P[inb]*dif[inb+1])/(dif[inb]-dif[inb+1]);
        lnb=pinb;
        pat=RD*dif[inb]*(P[inb]-pinb)/(P[inb]+pinb);
        tob=(T[inb]*(pinb-P[inb+1])+T[inb+1]*(P[inb]-pinb))/(P[inb]-P[inb+1]);
    }
    return {std::max(pa+pat-na,0.0),tob,lnb,1};
}

// `pi()` de tcpyPI para una columna: viento máximo (m/s) y presión mínima (hPa).
inline std::pair<double,double> column(double sstc,double msl,const std::vector<double>& P,
                                       const std::vector<double>& T,std::vector<double> R) {
    if (!(sstc>5.0&&sstc<=100.0)) return {NaN,NaN};
    if (P.empty()) return {NaN,NaN};
    for (double t:T) if (!(t>100.0)||t-273.15>100.0) return {NaN,NaN};
    for (double& r:R) if (std::isnan(r)) r=0.0;
    double sstk=sstc+273.15, es0=es_cc(sstc);
    Cape env=cape(T[0],R[0],P[0],T,R,P);
    double capea=env.cape;
    double pm=970.0, pmold=pm, pnew=0.0, rat=1.0, tvav=NaN;
    double capem=0, capems=0;
    int np_=0;
    while (std::abs(pnew-pmold)>0.5) {
        double pp=std::min(pm,1000.0);
        double rp=EPS*R[0]*msl/(pp*(EPS+R[0])-R[0]*msl);
        capem=cape(T[0],rp,pp,T,R,P).cape;
        rp=rv(es0,pp);
        Cape star=cape(sstk,rp,pp,T,R,P);
        capems=star.cape;
        rat=sstk/star.tob;
        double tv0=trho(T[0],R[0],R[0]), tvsst=trho(sstk,rp,rp);
        tvav=0.5*(tv0+tvsst);
        double cat=std::max((capem-capea)+0.5*CKCD*rat*(capems-capem),0.0);
        pnew=msl*std::exp(-cat/(RD*tvav));
        pmold=pm;
        pm=pnew;
        if (++np_>200||pm<400) return {NaN,NaN};
    }
    double catfac=0.5*(1.+1/B_EYE);
    double cat=std::max((capem-capea)+CKCD*rat*catfac*(capems-capem),0.0);
    double pmin=msl*std::exp(-cat/(RD*tvav));
    double vmax=V_REDUC*std::sqrt(CKCD*rat*std::max(0.0,capems-capem));
    return {vmax,pmin};
}
}  // namespace pi_detail

// Rejillas: sst (°C), msl y presión en superficie (hPa) de forma (filas,
// columnas); temperatura (°C) y razón de mezcla (g/kg) de forma (niveles,
// filas, columnas), con `pressure` (hPa) de mayor a menor. Libera el GIL.
py::tuple potential_intensity(
        py::array_t<double,py::array::c_style|py::array::forcecast> sst,
        py::array_t<double,py::array::c_style|py::array::forcecast> msl,
        py::array_t<double,py::array::c_style|py::array::forcecast> surface,
        py::array_t<double,py::array::c_style|py::array::forcecast> pressure,
        py::array_t<double,py::array::c_style|py::array::forcecast> temperature,
        py::array_t<double,py::array::c_style|py::array::forcecast> mixing) {
    if (temperature.ndim()!=3||mixing.ndim()!=3||sst.ndim()!=2||msl.ndim()!=2||surface.ndim()!=2||pressure.ndim()!=1)
        throw py::value_error("potential_intensity: dimensiones inesperadas");
    const py::ssize_t nk=temperature.shape(0), ny=temperature.shape(1), nx=temperature.shape(2);
    for (int axis=0;axis<3;++axis)
        if (mixing.shape(axis)!=temperature.shape(axis)) throw py::value_error("potential_intensity: T y r difieren");
    for (const auto* a:{&sst,&msl,&surface})
        if (a->shape(0)!=ny||a->shape(1)!=nx) throw py::value_error("potential_intensity: rejillas 2D distintas");
    if (pressure.shape(0)!=nk) throw py::value_error("potential_intensity: niveles distintos");
    for (py::ssize_t k=1;k<nk;++k)
        if (!(pressure.at(k)<pressure.at(k-1))) throw py::value_error("potential_intensity: la presión debe decrecer");
    std::vector<py::ssize_t> shape={ny,nx};
    py::array_t<double> vmax(shape), pmin(shape);
    const double *ps=pressure.data(), *ts=temperature.data(), *rs=mixing.data();
    const double *ss=sst.data(), *ms=msl.data(), *sp=surface.data();
    double *ov=vmax.mutable_data(), *op=pmin.mutable_data();
    {
        py::gil_scoped_release release;
        const py::ssize_t plane=ny*nx;
        std::vector<double> P,T,R;
        for (py::ssize_t i=0;i<plane;++i) {
            P.clear();T.clear();R.clear();
            bool hueco=false;
            for (py::ssize_t k=0;k<nk;++k) {
                if (std::isfinite(sp[i])&&ps[k]>sp[i]) continue;
                double t=ts[k*plane+i];
                if (!std::isfinite(t)) {hueco=true;break;}
                P.push_back(ps[k]);T.push_back(t+273.15);R.push_back(rs[k*plane+i]*1e-3);
            }
            auto res=hueco||!std::isfinite(ms[i])?std::pair<double,double>{NaN,NaN}
                                                  :pi_detail::column(ss[i],ms[i],P,T,R);
            ov[i]=res.first;op[i]=res.second;
        }
    }
    return py::make_tuple(vmax,pmin);
}
