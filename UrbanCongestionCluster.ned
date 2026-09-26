import simu5g.nodes.BackgroundCell;
import inet.networklayer.configurator.ipv4.Ipv4NetworkConfigurator;
import inet.networklayer.ipv4.RoutingTableRecorder;
import inet.node.ethernet.Eth10G;
import inet.node.inet.Router;
import inet.node.inet.StandardHost;
import simu5g.common.binder.Binder;
import simu5g.common.carrierAggregation.CarrierAggregation;
import simu5g.nodes.NrUe;
import simu5g.nodes.Upf;
import simu5g.nodes.gNodeB;
import simu5g.world.radio.LteChannelControl;

network UrbanCongestionCluster
{
    parameters:
        int numUEs = default(50);
        @display("bgb=1000,1000;bgg=100,1,grey95");

    submodules:
        channelControl: LteChannelControl {
            @display("p=50,25;is=s");
        }
        routingRecorder: RoutingTableRecorder {
            @display("p=50,75;is=s");
        }
        configurator: Ipv4NetworkConfigurator {
            @display("p=50,125;is=s");
        }
        binder: Binder {
            @display("p=50,175;is=s");
        }
        carrierAggregation: CarrierAggregation {
            @display("p=50,258;is=s");
        }
        server: StandardHost {
            @display("p=100,500;is=n;i=device/server");
        }
        router: Router {
            @display("p=183,347;i=device/smallrouter");
        }
        upf: Upf {
            @display("p=305,534");
        }
        iUpf: Upf {
            @display("p=466,359");
        }

        // Macro gNB: central, wide coverage
        macroGnb: gNodeB {
            @display("p=900,297;is=vl");
        }

        // 3 Micro gNBs at edges
        microGnb1: gNodeB {
            @display("p=700,200;is=s");
        }
        microGnb2: gNodeB {
            @display("p=900,700;is=s");
        }
        microGnb3: gNodeB {
            @display("p=500,700;is=s");
        }
        bgCell[4]: BackgroundCell {
            @display("p=466,139;is=l");
        }

        // 50 UEs
        ue[numUEs]: NrUe {
            @display("p=279,875;is=l");
        }

    connections allowunconnected:
        server.pppg++ <--> Eth10G <--> router.pppg++;
        router.pppg++ <--> Eth10G <--> upf.filterGate;
        upf.pppg++ <--> Eth10G <--> iUpf.pppg++;

        // Each gNB connects to iUpf
        iUpf.pppg++ <--> Eth10G <--> macroGnb.ppp;
        iUpf.pppg++ <--> Eth10G <--> microGnb1.ppp;
        iUpf.pppg++ <--> Eth10G <--> microGnb2.ppp;
        iUpf.pppg++ <--> Eth10G <--> microGnb3.ppp;
        // X2 mesh — all 6 gNB pairs
        macroGnb.x2++ <--> Eth10G <--> microGnb1.x2++;
        macroGnb.x2++ <--> Eth10G <--> microGnb2.x2++;
        macroGnb.x2++ <--> Eth10G <--> microGnb3.x2++;
        microGnb1.x2++ <--> Eth10G <--> microGnb2.x2++;
        microGnb1.x2++ <--> Eth10G <--> microGnb3.x2++;
        microGnb2.x2++ <--> Eth10G <--> microGnb3.x2++;
}
