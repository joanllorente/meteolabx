#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cmath>
#include <limits>
#include <vector>
#include <unordered_map>
#include <cstring>
namespace py = pybind11;
constexpr double NaN = std::numeric_limits<double>::quiet_NaN();
double thetae(double p, double t, double d) {
    if (!std::isfinite(p) || !std::isfinite(t) || !std::isfinite(d)) return NaN;
    d = std::min(d,t);
    double c=d-273.15, e=6.112*std::exp(17.67*c/(c+243.5));
    if (!(p>e)) return NaN;
    double r=(287.05/461.5)*e/(p-e);
    double l=1.0/(1.0/(d-56.0)+std::log(t/d)/800.0)+56.0;
    return t*std::pow(1000.0/std::max(p-e,1.0),0.2854*(1.0-0.28*r))
        *std::exp((3036.0/l-1.78)*r*(1.0+0.448*r));
}
double interp(const std::vector<double>& p,const std::vector<double>& logs,
              const std::vector<double>& v,double target) {
    // numpy.isclose(target, surface, atol=.5), including its default rtol.
    if (std::isfinite(target) && std::isfinite(p[0]) &&
        std::abs(target-p[0])<=0.5+1e-5*std::abs(p[0])) return v[0];
    double lt=std::log(std::max(target,1e-6));
    for (size_t k=0;k+1<p.size();++k) {
        if (std::isfinite(p[k]) && std::isfinite(p[k+1]) &&
            std::isfinite(v[k]) && std::isfinite(v[k+1]) &&
            p[k]>p[k+1]+0.1 && target<=p[k] && target>=p[k+1])
            return v[k]+(lt-logs[k])/(logs[k+1]-logs[k])*(v[k+1]-v[k]);
    }
    return NaN;
}
// Read native float32/float64 views without allocating a converted 3D array.
// memcpy also supports unaligned NumPy buffers safely.
struct Profile {
    py::buffer_info info;
    bool single;
    explicit Profile(const py::array& a): info(a.request()), single(a.dtype().is(py::dtype::of<float>())) {
        if (!single && !a.dtype().is(py::dtype::of<double>()))
            throw py::type_error("Profiles must use native float32 or float64");
        if (info.ndim!=3) throw py::value_error("Profiles must be three-dimensional");
    }
    py::ssize_t shape(int axis) const { return info.shape[axis]; }
    double operator()(py::ssize_t k,py::ssize_t y,py::ssize_t x) const {
        const char* ptr=static_cast<const char*>(info.ptr)+k*info.strides[0]+y*info.strides[1]+x*info.strides[2];
        if (single) { float v; std::memcpy(&v,ptr,sizeof(v)); return v; }
        double v; std::memcpy(&v,ptr,sizeof(v)); return v;
    }
};
// For non-increasing pressure, skip only intervals strictly above target.
// Equality stays on the first matching interval, as in the original scan.
double interp_forward(const std::vector<double>& p,const std::vector<double>& logs,
                      const std::vector<double>& v,double target,size_t& cursor,bool monotonic) {
    if (!monotonic) return interp(p,logs,v,target);
    if (std::abs(target-p[0])<=0.5+1e-5*std::abs(p[0])) return v[0];
    while (cursor+1<p.size() && p[cursor+1]>target) ++cursor;
    double lt=std::log(std::max(target,1e-6));
    for (size_t k=cursor;k+1<p.size();++k) {
        if (p[k]<target) break;
        if (std::isfinite(v[k]) && std::isfinite(v[k+1]) && p[k]>p[k+1]+.1 && target<=p[k] && target>=p[k+1])
            return v[k]+(lt-logs[k])/(logs[k+1]-logs[k])*(v[k+1]-v[k]);
    }
    return NaN;
}
py::tuple source(py::array pa,py::array ta,py::array da,py::array ha) {
    Profile p(pa),t(ta),d(da),h(ha);
    for(int axis=0;axis<3;++axis)
        if(p.shape(axis)!=t.shape(axis)||p.shape(axis)!=d.shape(axis)||p.shape(axis)!=h.shape(axis))
            throw py::value_error("Profiles must have identical shapes");
    if(p.shape(0)<1) throw py::value_error("At least one level is required");
    std::vector<py::ssize_t> shape={p.shape(1),p.shape(2)};
    py::array_t<int> indices(shape);
    py::array_t<double> ps(shape),ts(shape),ds(shape),hs(shape);
    auto oi=indices.mutable_unchecked<2>();auto op=ps.mutable_unchecked<2>();
    auto ot=ts.mutable_unchecked<2>();auto od=ds.mutable_unchecked<2>();auto oh=hs.mutable_unchecked<2>();
    {
        py::gil_scoped_release release;
        std::vector<double> pv(p.shape(0)),tv(p.shape(0)),dv(p.shape(0)),hv(p.shape(0)),lp(p.shape(0));
        std::unordered_map<double,double> memo;
        memo.reserve(p.shape(0)*21);
        for(py::ssize_t y=0;y<p.shape(1);++y) for(py::ssize_t x=0;x<p.shape(2);++x) {
            for(py::ssize_t k=0;k<p.shape(0);++k) {
                pv[k]=p(k,y,x);tv[k]=t(k,y,x);dv[k]=d(k,y,x);
                if (std::isnan(tv[k]) || std::isnan(dv[k])) dv[k]=NaN;
                else dv[k]=std::min(dv[k],tv[k]);hv[k]=h(k,y,x);
                lp[k]=std::log(std::max(pv[k],1e-6));
            }
            memo.clear();
            bool monotonic=std::isfinite(pv[0]);
            for(size_t k=1;k<pv.size();++k) monotonic=monotonic && std::isfinite(pv[k]) && pv[k]<=pv[k-1];
            bool reuse=false;
            for(size_t k=0;k<pv.size() && !reuse;++k)
                for(size_t l=k+1;l<pv.size();++l) {
                    double gap=std::abs(pv[k]-pv[l]);
                    if (gap<=100 && std::fmod(gap,5.0)==0) {reuse=true;break;}
                }
            double best=std::numeric_limits<double>::infinity();int idx=-1;
            for(size_t k=0;k<pv.size();++k) {
                double base=pv[k];
                if(!std::isfinite(base)||!(base<=pv[0]+0.5 && base>=pv[0]-400.0)) continue;
                double vals[21];bool valid=true;size_t cursor=0;
                for(int j=0;j<21;++j) {
                    double target=base-5*j;
                    auto cached=reuse?memo.find(target):memo.end();
                    if (cached!=memo.end()) vals[j]=cached->second;
                    else {
                        vals[j]=thetae(target,interp_forward(pv,lp,tv,target,cursor,monotonic),
                                             interp_forward(pv,lp,dv,target,cursor,monotonic));
                        if(reuse) memo.emplace(target,vals[j]);
                    }
                    if(!std::isfinite(vals[j])) {valid=false;break;}
                }
                if(!valid) continue;
                double middle=0;for(int j=1;j<20;++j) middle+=vals[j];
                double mean=(0.5*vals[0]+middle+0.5*vals[20])/20;
                if(mean<best) {best=mean;idx=static_cast<int>(k);}
            }
            double target=idx<0?NaN:pv[idx]-50.0;
            oi(y,x)=idx;op(y,x)=target;
            ot(y,x)=interp(pv,lp,tv,target);
            double td=interp(pv,lp,dv,target),tt=ot(y,x);
            od(y,x)=(!std::isfinite(td)||!std::isfinite(tt))?NaN:std::min(td,tt);
            oh(y,x)=interp(pv,lp,hv,target);
        }
    }
    return py::make_tuple(indices,ps,ts,ds,hs);
}
// Scalar thermodynamics adapted from SHARPpy 1.4.0a5 thermo.py.
// Copyright and redistribution terms: SHARPpy-LICENSE.rst.
constexpr double ROCP = 0.28571426;
double wobus(double t) {
    t -= 20;
    if (t <= 0) {
        double a = 1+t*(-8.841660499999999e-3+t*(1.4714143e-4+t*(-9.671989000000001e-7+t*(-3.2607217e-8+t*(-3.8598073e-10)))));
        return 15.13/std::pow(a,4);
    }
    double a=t*(4.9618922e-7+t*(-6.1059365e-9+t*(3.9401551e-11+t*(-1.2588129e-13+t*1.6688280e-16))));
    a=1+t*(3.6182989e-3+t*(-1.3603273e-5+a));
    return 29.93/std::pow(a,4)+.96*t-14.8;
}
double satlift_scalar(double p, double tm) {
    if (!(p>0) || !std::isfinite(p) || !std::isfinite(tm)) return NaN;
    if (std::abs(p-1000)<=.001) return tm;
    double pw=std::pow(p/1000,ROCP), t1=(tm+273.15)*pw-273.15;
    double e1=wobus(t1)-wobus(tm), rate=1;
    // Same conv=.1 as scalar SHARPpy, bounded for malformed profiles.
    for (int iteration=0; iteration<100; ++iteration) {
        double t2=t1-e1*rate;
        double e2=(t2+273.15)/pw-273.15;
        e2+=wobus(t2)-wobus(e2)-tm;
        double error=e2*rate;
        if (!std::isfinite(error)) return NaN;
        if (std::abs(error)<=.1) return t2-error;
        rate=(t2-t1)/(e2-e1);t1=t2;e1=e2;
    }
    throw std::runtime_error("DCAPE satlift did not converge in 100 iterations");
}
double wetlift_scalar(double p,double t,double target) {
    if (!(p>0) || !std::isfinite(t)) return NaN;
    double theta=(t+273.15)*std::pow(1000/p,ROCP)-273.15;
    return satlift_scalar(target,theta-wobus(theta)+wobus(t));
}
double wetbulb_scalar(double p,double t,double td) {
    double s=t-td;
    double tl=t-s*(1.2185+.001278*t+s*(-.00219+1.173e-5*s-.0000052*t));
    double theta=(t+273.15)*std::pow(1000/p,ROCP)-273.15;
    double pl=1000/std::pow((theta+273.15)/(tl+273.15),1/ROCP);
    return wetlift_scalar(pl,tl,p);
}
// Same bounded Newton method and residual threshold as the Python solver.
double saturated_temperature(double p,double target,double initial) {
    double value=std::isnan(initial)?NaN:std::max(170.0,std::min(initial,380.0));
    double goal=std::log(std::isnan(target)?NaN:std::max(target,1.0));
    for (int iteration=0;iteration<9;++iteration) {
        double residual=std::log(thetae(p,value,value))-goal;
        if (!std::isfinite(residual) || std::abs(residual)<1e-7) break;
        double plus=value+.08, minus=value-.08;
        double derivative=(std::log(thetae(p,plus,plus))-std::log(thetae(p,minus,minus)))/.16;
        double correction=std::isfinite(derivative) && std::abs(derivative)>1e-8 ? residual/derivative:0;
        value=std::max(170.0,std::min(value-correction,380.0));
    }
    return value;
}
// SHARPpy params.ship plus the existing adapter's scalar conversions.
// See SHARPpy-LICENSE.rst; retain the round trips, which are not exact inverses.
double ship_scalar(double cape,double ratio,double lapse,double temp,double shear,double freezing) {
    if (!std::isfinite(cape)||!std::isfinite(ratio)||!std::isfinite(lapse)||
        !std::isfinite(temp)||!std::isfinite(shear)||!std::isfinite(freezing)) return NaN;
    cape=std::max(cape,0.0);ratio=std::max(ratio,0.0);
    double x=std::log10(ratio*1000/(622+ratio));
    double td=(std::pow(10.,.0498646455*x+2.4082965)-7.07475+
               38.9114*std::pow(std::pow(10.,.0915*x)-1.2035,2))-273.15;
    double pol=td*(1.1112018e-17+td*-3.0994571e-20);
    pol=td*(2.1874425e-13+td*(-1.789232e-15+pol));
    pol=td*(4.3884180e-9+td*(-2.988388e-11+pol));
    pol=td*(7.8736169e-5+td*(-6.111796e-7+pol));
    pol=.99999683+td*(-9.082695e-3+pol);
    x=.02*(td-12.5+7500./1000.);
    double vapor=(1.+.0000045*1000.+.0014*x*x)*(6.1078/std::pow(pol,8));
    double mr=621.97*(vapor/(1000.-vapor));
    double knots=std::max(shear,0.0)*1.94384449;
    shear=std::sqrt(knots*knots)*.514444;
    shear=std::max(7.,std::min(shear,27.));
    mr=std::max(11.,std::min(mr,13.6));
    lapse=std::max(lapse,1e-9);freezing=std::max(freezing,1e-9);
    if (temp==0) temp=1e-9;
    if (temp>-5.5) temp=-5.5;
    double value=-1.*(cape*mr*lapse*temp*shear)/42000000.;
    if (cape<1300) value=value*(cape/1300.);
    if (lapse<5.8) value=value*(lapse/5.8);
    if (freezing<2400) value=value*(freezing/2400.);
    return std::isnan(value)?NaN:std::max(value,0.);
}
#include "parcel.hpp"
PYBIND11_MODULE(_dcape_native,m) {
    m.def("parcel", &parcel_diagnostics_native);
    m.def("saturated_temperature", py::vectorize(saturated_temperature));
    m.def("ship", py::vectorize(ship_scalar));
    m.def("wetlift", py::vectorize(wetlift_scalar));
    m.def("wetbulb", py::vectorize(wetbulb_scalar));
    m.def("satlift", py::vectorize(satlift_scalar));
    m.def("source",&source,py::arg("pressure").noconvert(),py::arg("temperature").noconvert(),
          py::arg("dewpoint").noconvert(),py::arg("height").noconvert());
}
