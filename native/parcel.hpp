// Column-local translation of convective_diagnostics.parcel_diagnostics.
// Eight surface outputs; scratch vectors scale with vertical levels only.
constexpr double PARCEL_KAPPA=287.05/1004.0, PARCEL_EPS=287.05/461.5, G=9.80665;
double parcel_ratio(double p,double td) {
    double c=td-273.15, e=6.112*std::exp(17.67*c/(c+243.5));
    return std::isfinite(p)&&std::isfinite(e)&&p>e ? PARCEL_EPS*e/(p-e):NaN;
}
double virtual_t(double t,double r) { return t*(1+r/PARCEL_EPS)/(1+r); }
double nan_min(double a,double b) {return std::isnan(a)||std::isnan(b)?NaN:std::min(a,b);}
py::tuple parcel_diagnostics_native(py::array pa,py::array ta,py::array da,py::array ha,
                                   py::array po,py::array to,py::array de) {
    Profile p(pa),t(ta),d(da),h(ha),op(po),ot(to),od(de);
    for(int axis=0;axis<3;++axis)
        if(p.shape(axis)!=t.shape(axis)||p.shape(axis)!=d.shape(axis)||p.shape(axis)!=h.shape(axis))
            throw py::value_error("Profiles must have identical shapes");
    for(const Profile* origin:{&op,&ot,&od})
        if(origin->shape(0)!=1||origin->shape(1)!=p.shape(1)||origin->shape(2)!=p.shape(2))
            throw py::value_error("Parcel origins must have shape (1, rows, columns)");
    if(p.shape(0)<1||p.shape(1)<1||p.shape(2)<1) throw py::value_error("Profiles must not be empty");
    int index500=-1;double distance=std::numeric_limits<double>::infinity();
    for(int k=0;k<p.shape(0);++k) {
        double diff=std::abs(p(k,0,0)-500.);
        if(!std::isnan(diff)&&(index500<0||diff<distance)) {index500=k;distance=diff;}
    }
    if(index500<0) throw py::value_error("All-NaN pressure column");
    std::vector<py::array_t<double>> outputs;
    for(int i=0;i<8;++i) outputs.emplace_back(std::vector<py::ssize_t>{p.shape(1),p.shape(2)});
    double* out[8];for(int i=0;i<8;++i) out[i]=outputs[i].mutable_data();
    {
        py::gil_scoped_release release;
        size_t n=p.shape(0);
        std::vector<double> pv(n),z(n),b(n),pt(n);
        for(py::ssize_t y=0;y<p.shape(1);++y) for(py::ssize_t x=0;x<p.shape(2);++x) {
            double p0=op(0,y,x),t0=ot(0,y,x),td0=nan_min(od(0,y,x),t0);
            double tlcl=1./(1./(td0-56.)+std::log(t0/td0)/800.)+56.;
            double plcl=p0*std::pow(tlcl/t0,1./PARCEL_KAPPA);
            double te=thetae(p0,t0,td0),r0=parcel_ratio(p0,od(0,y,x));
            for(size_t k=0;k<n;++k) {
                pv[k]=p(k,y,x);z[k]=h(k,y,x);
                double value=NaN;
                if(std::isfinite(pv[k])&&std::isfinite(t0)&&std::isfinite(td0)&&pv[k]<=p0+.5) {
                    if(pv[k]>=plcl) value=t0*std::pow(pv[k]/p0,PARCEL_KAPPA);
                    else value=saturated_temperature(pv[k],te,tlcl*std::pow(pv[k]/plcl,.16));
                }
                pt[k]=value;
                double er=parcel_ratio(pv[k],nan_min(d(k,y,x),t(k,y,x)));
                double pr=pv[k]>=plcl?r0:parcel_ratio(pv[k],value);
                double ev=virtual_t(t(k,y,x),er), v=virtual_t(value,pr);
                b[k]=G*(v-ev)/ev;
            }
            double lcl=NaN,lfc=NaN,lfcp=NaN;
            for(size_t k=0;k+1<n;++k) {
                if(!std::isfinite(lcl)&&std::isfinite(pv[k])&&std::isfinite(pv[k+1])&&
                   std::isfinite(z[k])&&std::isfinite(z[k+1])&&std::isfinite(plcl)&&
                   pv[k]>pv[k+1]&&z[k+1]>z[k]&&pv[k]>=plcl&&pv[k+1]<=plcl) {
                    double f=std::log(pv[k]/plcl)/std::log(pv[k]/pv[k+1]);
                    lcl=z[k]+std::max(0.,std::min(f,1.))*(z[k+1]-z[k]);
                }
            }
            for(size_t k=0;k+1<n;++k) {
                if(std::isfinite(lfc)||!std::isfinite(lcl)||!std::isfinite(pv[k])||!std::isfinite(pv[k+1])||
                   !std::isfinite(z[k])||!std::isfinite(z[k+1])||!std::isfinite(b[k])||!std::isfinite(b[k+1])||
                   !(pv[k]>pv[k+1]&&z[k+1]>z[k]&&z[k+1]>=lcl)) continue;
                double start=std::max(z[k],lcl), f=(start-z[k])/(z[k+1]-z[k]);
                double start_b=b[k]+f*(b[k+1]-b[k]);
                if(start_b>0) lfc=start;
                else if(start_b<=0&&b[k+1]>0) lfc=start+(-start_b/(b[k+1]-start_b))*(z[k+1]-start);
                if(std::isfinite(lfc)) lfcp=pv[k]*std::pow(pv[k+1]/pv[k],(lfc-z[k])/(z[k+1]-z[k]));
            }
            double el=NaN,elp=NaN,top=-std::numeric_limits<double>::infinity();
            bool has=false,incomplete=false;
            for(size_t k=0;k+1<n;++k) {
                bool layer=pv[k]<=p0+.5&&pv[k]>pv[k+1];
                bool valid=layer&&z[k+1]>z[k]&&std::isfinite(z[k])&&std::isfinite(z[k+1])&&std::isfinite(b[k])&&std::isfinite(b[k+1]);
                incomplete|=layer&&!valid;has|=valid;
                if(!valid) continue;
                top=z[k+1];
                if(z[k+1]>lfc) {
                    if(b[k+1]>0) {el=NaN;elp=NaN;}
                    if(b[k]>0&&b[k+1]<=0) {
                        double f=b[k]/(b[k]-b[k+1]);
                        el=z[k]+f*(z[k+1]-z[k]);elp=pv[k]*std::pow(pv[k+1]/pv[k],f);
                    }
                }
            }
            double cape=0,cin=0,ceiling=std::isfinite(el)?el:top;
            for(size_t k=0;k+1<n;++k) {
                double dz=z[k+1]-z[k];
                if(!(pv[k]<=p0+.5&&dz>0&&std::isfinite(b[k])&&std::isfinite(b[k+1]))) continue;
                auto energy=[&](double lower,double upper) {
                    if(std::isnan(lower)||std::isnan(upper)||std::isnan(z[k])||std::isnan(z[k+1])) return 0.;
                    double start=std::max(z[k],lower),end=std::min(z[k+1],upper);
                    if(!(end>start)) return 0.;
                    double f0=(start-z[k])/dz,f1=(end-z[k])/dz;
                    return (b[k]+.5*(f0+f1)*(b[k+1]-b[k]))*(end-start);
                };
                cape+=energy(lfc,ceiling);cin+=energy(z[k],lfc);
            }
            size_t i=y*p.shape(2)+x;bool usable=has&&!incomplete;
            out[0][i]=usable?(std::isnan(cape)?NaN:std::max(cape,0.)):NaN;
            out[1][i]=usable?(std::isnan(cin)?NaN:std::min(cin,0.)):NaN;
            out[2][i]=std::isfinite(pt[index500])?t(index500,y,x)-pt[index500]:NaN;
            out[3][i]=usable?el:NaN;out[4][i]=usable?elp:NaN;
            out[5][i]=lfc;out[6][i]=lfcp;out[7][i]=lcl;
        }
    }
    py::tuple result(8);for(int i=0;i<8;++i) result[i]=outputs[i];return result;
}
