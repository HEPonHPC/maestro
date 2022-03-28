import pandas
from mfstrodf.mpi4py_ import MPI_
import time, datetime
import sys,os,json
from mfstrodf import OutputLevel,Settings
import pprint
class ModelConstruction(object):
    def __init__(self,state,mc_run_folder):
        self.state:Settings = state
        self.mc_run_folder = mc_run_folder
        self.debug = OutputLevel.is_debug(self.state.output_level)
        (self.mc_data_df,self.additional_data) = self.state.mc_object.convert_mc_output_to_df(self.mc_run_folder)
        self.state.set_data_names(list(self.mc_data_df.columns.values))
        """
        Code works for 
        Code works for 
        >inp = {'MC':{'bin1.P':[[1,2],[3,4],[6,3]],'bin1.V':[19,18,17],'bin2.P':[[1,2],[3,4],[6,3]],'bin2.V':[29,28,27]},
                'DMC':{'bin1.P':[[1,2],[3,4],[6,3]],'bin1.V':[99,98,97],'bin2.P':[[1,2],[3,4],[6,3]],'bin2.V':[89,88,87]}}
        >df = pd.DataFrame(inp)
        >df
                        MC                      DMC    
        bin1.P        [[1,2],[3,4],[6,3]]     [[1,2],[3,4],[6,3]]    
        bin1.V        [19, 18, 17]               [99, 98, 97]
        bin2.P        [[1,2],[3,4],[6,3]]     [[1,2],[3,4],[6,3]]    
        bin2.V        [29, 28, 27]              [89, 88, 87]     
        
        or its transpose
        MC,DMC are data_names. bin1.P,bin1,V,bin2.P,bin2.V are column names 
        
        additional_data can be None and even if it exists may not contain the required keys. always check
        1. whether additional_data is none 
        2. if not, then whether the required key exists in additional data 
        { MC:{xmin:[],xmax:[]}, DMC:{xmin:[],xmax:[]}}
        
        """
    def consturct_models(self):
        for data_name in self.state.data_names:
            fh = self.state.get_model_function_handle(data_name)
            fh(self,data_name) if fh is not None else self.appr_pa_m_construct(data_name)
    #TODO to implement. We need this for calculating values that were nan/inf of MC(\widetilde{\p}^{(k+1})
    def get_model_objects(self):
        pass

    #TODO: === Change appr_* functions after changes to apprentice
    def appr_pa_m_construct(self,data_name):
        self.appr_appx_construct(data_name)
    def appr_ra_m_n_construct(self,data_name):
        self.appr_appx_construct(data_name)
    def appr_ra_m_1_construct(self,data_name):
        self.appr_appx_construct(data_name)

    def appr_appx_construct(self,data_name):
        t4 = time.time()
        app = {}
        appscaled = {}
        comm = MPI_.COMM_WORLD
        rank = comm.Get_rank()
        columnnames = list(self.mc_data_df.index)

        import apprentice
        Sclocal = apprentice.Scaler(self.mc_data_df[data_name]['{}'.format(columnnames[0])],
                                    pnames=self.state.param_names)
        self.state.set_tr_center_scaled(Sclocal.scale(self.state.tr_center).tolist())
        self.state.set_scaled_min_max_parameter_bounds(Sclocal.box_scaled[:,0].tolist(),Sclocal.box_scaled[:,1].tolist())
        X_indicies = [cnum for cnum in range(0,len(columnnames),2)]
        Y_indicies = [cnum+1 for cnum in range(0,len(columnnames),2)]
        assert(len(X_indicies) == len(Y_indicies))
        rank_indicies = None
        if rank == 0:
            rank_indicies = MPI_.chunk_it([i for i in range(len(X_indicies))])
        rank_indicies = comm.scatter(rank_indicies, root=0)
        for ri in rank_indicies:
            X = self.mc_data_df[data_name]['{}'.format(columnnames[X_indicies[ri]])]
            Y = self.mc_data_df[data_name]['{}'.format(columnnames[Y_indicies[ri]])]
            m = self.state.model_parameters[data_name]['m']\
                if data_name in self.state.model_parameters and 'm' in self.state.model_parameters[data_name] \
                else 1
            n = self.state.model_parameters[data_name]['n'] \
                if data_name in self.state.model_parameters and 'n' in self.state.model_parameters[data_name] \
                else 0
            if self.debug:
                if ((ri + 1) % 5 == 0):
                    now = time.time()
                    tel = now - t4
                    ttg = tel * (len(columnnames)/2 - ri) / (ri + 1)
                    eta = now + ttg
                    eta = datetime.datetime.fromtimestamp(now + ttg)
                    sys.stdout.write(
                        "{}[{}] {}/{} (elapsed: {:.1f}s, to go: {:.1f}s, ETA: {})\r".format(
                            80 * " " if rank > 0 else "", rank, ri + 1, len(columnnames)/2, tel, ttg,
                            eta.strftime('%Y-%m-%d %H:%M:%S')), )
                    sys.stdout.flush()
            try:
                val = apprentice.RationalApproximation(X, Y, order=(m,n), pnames=self.state.param_names)
                if self.additional_data is not None and data_name in self.additional_data:
                    if '_xmin' in self.additional_data[data_name] and '_xmax' in self.additional_data[data_name]:
                        val._xmin = self.additional_data[data_name]["_xmin"][ri]
                        val._xmax = self.additional_data[data_name]["_xmax"][ri]

                Xscaled = [Sclocal.scale(x) for x in X]
                valscaled = apprentice.RationalApproximation(Xscaled, Y, order=(m,n), pnames=self.state.param_names)
            except AssertionError as error:
                raise(error)
            term_name = columnnames[X_indicies[ri]].split('.')[0]
            app[term_name] = val.asDict
            appscaled[term_name] = valscaled.asDict
        all_apps = comm.gather(app, root=0)
        all_apps_scaled = comm.gather(appscaled, root=0)
        val_out_file = self.state.working_directory.get_log_path(
            "{}_model_k{}.json".format(data_name,self.state.k))
        scaled_val_out_file = self.state.working_directory.get_log_path(
            "{}_model_scaled_k{}.json".format(data_name,self.state.k))
        if rank == 0:
            t5 = time.time()
            if self.debug: print("Approximation calculation took {} seconds".format(t5 - t4))
            sys.stdout.flush()
            from collections import OrderedDict
            JD = OrderedDict()
            a = {}
            for apps in all_apps:
                a.update(apps)
            for k in a.keys():
                JD[k] = a[k]

            with open(val_out_file, "w") as f:
                json.dump(JD, f,indent=4)

            JD = OrderedDict()
            a = {}
            for apps in all_apps_scaled:
                a.update(apps)
            for k in a.keys():
                JD[k] = a[k]
            with open(scaled_val_out_file, "w") as f:
                json.dump(JD, f,indent=4)

        self.state.update_f_structure_model_parameters('model',{data_name:val_out_file})
        self.state.update_f_structure_model_parameters('model_scaled',{data_name:scaled_val_out_file})
